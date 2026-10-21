"""UI liveness regressions, isolated from the operating DB and broker."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import kis_msj.ui_service as service_module
from kis_msj.storage import StateStore
from kis_msj.ui_server import INDEX_HTML, build_server
from kis_msj.ui_service import UIService


def _service(tmp_path):
    db=tmp_path/"state.sqlite3"
    config=tmp_path/"config.json"
    config.write_text(json.dumps({"stocks":[{"code":"005930","name":"Samsung"}],
                                  "storage_path":str(db),"log_path":str(tmp_path/"bot.log")}),encoding="utf-8")
    return UIService(config,tmp_path/"runtime.json"),config,db


def test_cache_replaces_old_db_versions_and_bounds_unrelated_keys(tmp_path):
    service,_,_=_service(tmp_path)
    for version in range(512):
        service._cache_set(("table","state.sqlite3","lots",(version,)),[version])
    versions=[key for key in service._cache if key[:3]==("table","state.sqlite3","lots")]
    assert len(versions)==1
    assert service._cache_get(versions[0])==[511]
    for page in range(512):
        service._cache_set(("independent_page",page),page)
    assert len(service._cache)<=service_module.UI_CACHE_MAX_ENTRIES


def test_cache_prunes_max_age_and_stamps_completion_not_load_start(tmp_path,monkeypatch):
    service,_,_=_service(tmp_path)
    clock=[10.]
    monkeypatch.setattr(service_module.time,"monotonic",lambda:clock[0])
    service._cache_set(("obsolete",),1)
    clock[0]+=service_module.UI_CACHE_MAX_AGE_SECONDS+1
    service._cache_set(("fresh",),2)
    assert ("obsolete",) not in service._cache
    calls=[]
    def load():
        calls.append(1)
        clock[0]+=100
        return {"value":len(calls)}
    result=service._cached_load(("slow",),load,ttl_seconds=10)
    clock[0]+=1
    assert service._cached_load(("slow",),load,ttl_seconds=10)==result
    assert len(calls)==1
    clock[0]+=10
    assert service._cached_load(("slow",),load,ttl_seconds=10)=={"value":2}


def test_concurrent_cache_misses_share_one_load(tmp_path):
    service,_,_=_service(tmp_path)
    barrier=threading.Barrier(8)
    started,release=threading.Event(),threading.Event()
    calls=[]
    def load():
        calls.append(1);started.set()
        assert release.wait(10)
        return {"complete":True}
    def request():
        barrier.wait(timeout=10)
        return service._cached_load(("concurrent",),load)
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending=[pool.submit(request) for _ in range(8)]
        try:
            assert started.wait(5)
        finally:
            release.set()
        assert [future.result(timeout=5) for future in pending]==[{"complete":True}]*8
    assert len(calls)==1
    assert not service._cache_inflight


def test_slow_cache_key_does_not_hold_the_global_lock(tmp_path):
    service,_,_=_service(tmp_path)
    started,release=threading.Event(),threading.Event()
    def slow():
        started.set()
        assert release.wait(10)
        return "slow"
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(service._cached_load,("slow",),slow)
        try:
            assert started.wait(5)
            fast=pool.submit(service._cached_load,("different",),lambda:"responsive")
            assert fast.result(timeout=3)=="responsive"
        finally:
            release.set()
        assert pending.result(timeout=5)=="slow"


def test_failed_cache_load_releases_waiters_and_can_retry(tmp_path):
    service,_,_=_service(tmp_path)
    def fail():
        raise sqlite3.OperationalError("temporary busy")
    with pytest.raises(sqlite3.OperationalError,match="temporary busy"):
        service._cached_load(("retry",),fail)
    assert not service._cache_inflight
    assert service._cached_load(("retry",),lambda:"recovered")=="recovered"
    assert not service._cache_inflight


def test_cache_clear_during_load_does_not_restore_stale_data(tmp_path):
    service,_,_=_service(tmp_path)
    started,release=threading.Event(),threading.Event()
    def old():
        started.set()
        assert release.wait(10)
        return "before invalidation"
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(service._cached_load,("changing",),old)
        try:
            assert started.wait(5)
            service._cache_clear()
        finally:
            release.set()
        assert pending.result(timeout=5)=="before invalidation"
    assert service._cache_get(("changing",)) is None
    assert service._cached_load(("changing",),lambda:"after invalidation")=="after invalidation"


def test_latest_prices_refresh_when_only_wal_changes(tmp_path):
    service,_,db=_service(tmp_path)
    StateStore(db)
    with sqlite3.connect(db) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO price_snapshots(code,sampled_at,current_price) VALUES(?,?,?)",("005930","2026-10-21T10:00:00",10000))
        writer.commit()
        first=service._latest_price_snapshot_rows({"005930"})
        assert first[0]["current_price"]==10000
        before=(db.stat().st_mtime_ns,db.stat().st_size)
        writer.execute("INSERT INTO price_snapshots(code,sampled_at,current_price) VALUES(?,?,?)",("005930","2026-10-21T10:01:00",11000))
        writer.commit()
        assert (db.stat().st_mtime_ns,db.stat().st_size)==before
        latest=service._latest_price_snapshot_rows({"005930"})
        assert latest[0]["current_price"]==11000
        assert latest[0]["id"]>first[0]["id"]
        latest[0]["current_price"]=1
        assert service._latest_price_snapshot_rows({"005930"})[0]["current_price"]==11000


def test_latest_price_lookup_has_bounded_work_with_large_history(tmp_path,monkeypatch):
    service,_,db=_service(tmp_path)
    StateStore(db)
    with sqlite3.connect(db) as connection:
        connection.executemany("INSERT INTO price_snapshots(code,sampled_at,current_price) VALUES(?,?,?)",
                               [("005930","2026-10-21T10:00:00",10000+i) for i in range(15000)])
    original_connect=sqlite3.connect
    progress_calls=[]
    def connect(*args,**kwargs):
        connection=original_connect(*args,**kwargs)
        def progress():
            progress_calls.append(1)
            return int(len(progress_calls)>30)
        connection.set_progress_handler(progress,1000)
        return connection
    monkeypatch.setattr(service_module.sqlite3,"connect",connect)
    rows=service._latest_price_snapshot_rows({"005930"})
    assert len(rows)==1 and rows[0]["current_price"]==24999
    assert len(progress_calls)<=30


def test_slow_chart_request_does_not_block_status(tmp_path,monkeypatch):
    _,config,_=_service(tmp_path)
    server=build_server(config,"127.0.0.1",0,tmp_path/"runtime.json")
    started,release=threading.Event(),threading.Event()
    service=server.RequestHandlerClass.service
    def slow_chart(*args,**kwargs):
        started.set()
        assert release.wait(10)
        return {"rows":[]}
    monkeypatch.setattr(service,"portfolio_history_chart",slow_chart)
    monkeypatch.setattr(service,"status",lambda:{"responsive":True})
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def get(path):
        connection=http.client.HTTPConnection(*server.server_address,timeout=5)
        try:
            connection.request("GET",path)
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:
            connection.close()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            chart=pool.submit(get,"/api/portfolio-dashboard/history-chart")
            try:
                assert started.wait(5)
                assert get("/api/status")== (200,{"responsive":True})
            finally:
                release.set()
            assert chart.result(timeout=5)[0]==200
    finally:
        release.set();server.shutdown();server.server_close();thread.join(timeout=5)


def test_second_ui_server_cannot_bind_same_port(tmp_path):
    _,config,_=_service(tmp_path)
    first=build_server(config,"127.0.0.1",0,tmp_path/"runtime.json")
    try:
        with pytest.raises(OSError):
            duplicate=build_server(config,"127.0.0.1",first.server_address[1],tmp_path/"runtime.json")
            duplicate.server_close()
    finally:
        first.server_close()


def _node():
    env=dict(os.environ)
    binary=shutil.which("node")
    if binary:
        return binary,env
    code=Path.home()/"AppData/Local/Programs/Microsoft VS Code/Code.exe"
    if code.is_file():
        env["ELECTRON_RUN_AS_NODE"]="1"
        return str(code),env
    pytest.skip("Node or VS Code Electron Node mode is unavailable")


def test_entire_inline_dashboard_javascript_parses():
    binary,env=_node()
    script=INDEX_HTML.split("<script>",1)[1].split("</script>",1)[0]
    checked=subprocess.run([binary,"--check"],input=script,text=True,encoding="utf-8",errors="replace",
                           capture_output=True,env=env,timeout=20)
    assert checked.returncode==0,checked.stdout+checked.stderr


def test_browser_reads_timeout_retry_dedupe_and_ignore_old_views():
    binary,env=_node()
    script=INDEX_HTML.split("<script>",1)[1].split("</script>",1)[0]
    helper=script[:script.index("function esc(")]
    refresh=script[script.index("let autoRefreshTimer"):script.index("const LABELS =")]
    view=script[script.index("let currentView ="):script.index("async function reloadCurrent")]
    harness=r"""
const assert=require('node:assert/strict');
const vm=require('node:vm');
const payload=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const setup=`
const nodes={connectionError:{hidden:true,textContent:''},lastRefreshAt:{textContent:''},
 autoRefreshEnabled:{checked:true},autoRefreshSeconds:{value:3}};
const document={getElementById:id=>nodes[id]||null};
const window={addEventListener:()=>{}};
let timers=new Map(),intervals=new Map(),sequence=0,calls=[],implementation;
function setTimeout(callback,ms){const id=++sequence;timers.set(id,{callback,ms});return id;}
function clearTimeout(id){timers.delete(id);}
function setInterval(callback,ms){const id=++sequence;intervals.set(id,{callback,ms});return id;}
function clearInterval(id){intervals.delete(id);}
function fetch(path,options){calls.push({path,options});return implementation(path,options);}
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
`;
const checks=`
(async()=>{
 let resolveRead;implementation=()=>new Promise(resolve=>resolveRead=resolve);
 const first=api('/same'),second=api('/same');assert.equal(calls.length,1);
 resolveRead(response({value:1}));await Promise.all([first,second]);await flush();
 assert.equal(readRequests.size,0);assert.equal(timers.size,0);
 implementation=()=>new Promise(()=>{});
 const timeout=api('/timeout');const timer=[...timers.values()].find(t=>t.ms===20000);
 assert.ok(timer);timer.callback();await assert.rejects(timeout);await flush();
 assert.equal(calls.at(-1).options.signal.aborted,true);assert.equal(readRequests.size,0);
 implementation=async()=>response({recovered:true});assert.equal((await api('/timeout')).recovered,true);
 implementation=async()=>response({error:'busy'},503);await assert.rejects(api('/busy'));await flush();
 implementation=async()=>response({ok:true});assert.equal((await api('/busy')).ok,true);
 let before=calls.length;
 await Promise.all([api('/mutation',{method:'POST'}),api('/mutation',{method:'POST'})]);
 assert.equal(calls.length-before,2);assert.equal(calls.at(-1).options.signal,undefined);
 implementation=async()=>{throw new Error('mutation failure');};before=calls.length;
 await assert.rejects(api('/mutation',{method:'POST'}));assert.equal(calls.length-before,1);
 clearConnectionError();let resolveOld,rendered=false;
 implementation=()=>new Promise(resolve=>resolveOld=resolve);
 const old=api('/old-view').then(()=>{rendered=true;},error=>assert.equal(error.name,'StaleViewError'));
 selectView('config');resolveOld(response({old:true}));await old;assert.equal(rendered,false);
 assert.equal(nodes.connectionError.hidden,true);
 let reloads=0;
 refreshBanner=async()=>api('/status',{viewIndependent:true});reloadCurrent=async()=>{reloads++;};
 implementation=()=>new Promise(()=>{});before=calls.length;
 const blocked=manualRefresh();await manualRefresh();assert.equal(calls.length-before,1);
 [...timers.values()].find(t=>t.ms===20000).callback();await blocked;assert.equal(refreshInFlight,false);
 implementation=async()=>response({ok:true});await manualRefresh();assert.equal(reloads,1);
 assert.equal(refreshInFlight,false);assert.equal(nodes.connectionError.hidden,true);
 let resolveStatus;implementation=()=>new Promise(resolve=>resolveStatus=resolve);
 const switching=manualRefresh();selectView('stocks');resolveStatus(response({ok:true}));
 await switching;assert.equal(reloads,1);assert.equal(refreshInFlight,false);
 selectView('config');setupAutoRefresh();const tick=[...intervals.values()][0].callback;
 before=calls.length;tick();assert.equal(calls.length,before);
 selectView('stocks');let resolvePoll;implementation=()=>new Promise(resolve=>resolvePoll=resolve);
 tick();tick();assert.equal(calls.length-before,1);resolvePoll(response({ok:true}));await flush();
 assert.equal(refreshInFlight,false);assert.equal(readRequests.size,0);
 console.log('ui_async_checks_passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
`;
vm.runInNewContext(setup+payload.helper+payload.refresh+payload.view+checks,{assert,AbortController,console,process});
"""
    checked=subprocess.run([binary,"-e",harness],input=json.dumps(dict(helper=helper,refresh=refresh,view=view)),
                           text=True,encoding="utf-8",errors="replace",capture_output=True,env=env,timeout=20)
    assert checked.returncode==0,checked.stdout+checked.stderr
    assert "ui_async_checks_passed" in checked.stdout,checked.stdout+checked.stderr
