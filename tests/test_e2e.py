import csv,io,json
from app.pipeline import execute,load_sample
from app import store
def test_full_sample_flow(client):
    r=client.post("/api/runs",json={"sample":"dirty.csv"});assert r.status_code==200
    p=r.get_json();assert len(p["stages"])==8
    assert p["stages"][5]["metrics"]["balanced"] is True
def test_idempotent_real_input(sample):
    a=execute(sample);b=execute(sample)
    assert a["run_id"]==b["run_id"] and len(store.history())==1
def test_stale_review_409(client):
    "TRAP-26 陈旧复核不得覆盖新版本"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json();u="/api/runs/"+p["run_id"]+"/review"
    assert client.post(u,json={"version":1,"decision":"已复核"}).status_code==200
    assert client.post(u,json={"version":1,"decision":"退回复核"}).status_code==409
def test_export_has_manifest_and_quarantine(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    r=client.get("/api/runs/"+p["run_id"]+"/archive")
    rows=list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert rows[0][1]=="input_sha256" and any(x[3]=="quarantine" for x in rows[1:])
def test_failed_import_is_atomic(client):
    assert client.post("/api/runs",json={"csv":"bad"}).status_code==422
    assert store.history()==[]
def test_module_entrances(client):
    from app.config import MODULES
    for slug,name in MODULES:
        assert client.get("/api/"+slug).status_code==200
        assert client.get("/"+slug).status_code==200
def test_missing_run_404(client): assert client.get("/api/runs/missing").status_code==404
def test_archive_reuses_retrieval_decision(client):
    "归档凭据与检索页面共用 M8 裁决：已撤文档标 suppressed 且不可命中"
    ledger=[{"doc_id":"0090","rebuild_state":"PENDING","scope_epoch":"e1","ledger_version":"v1"},
            {"doc_id":"0091","rebuild_state":"REBUILT","scope_epoch":"e1","ledger_version":"v1"}]
    p=client.post("/api/runs",json={"sample":"dirty.csv","parameters":{"migration_ledger":ledger}}).get_json()
    m8={r["doc_id"]:r for r in p["stages"][7]["rows"]}
    r=client.get("/api/runs/"+p["run_id"]+"/archive")
    rows=list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    header=rows[0];assert header[6]=="retrieval_route" and header[7]=="searchable"
    evidence={}
    for x in rows[1:]:
        if x[3] in {"accepted","suppressed"}: evidence[json.loads(x[8])["doc_id"]]=x
    archived_0001=evidence["0001"]
    assert archived_0001[3]=="suppressed" and archived_0001[6]==m8["0001"]["retrieval_route"]
    assert archived_0001[7]==m8["0001"]["searchable"]=="0"
    archived_0090=evidence["0090"]
    assert archived_0090[6]=="LEGACY_FALLBACK" and archived_0090[7]=="1"
def test_retrieval_page_and_api_share_decision(client):
    assert client.get("/api/retrieval").status_code==200
    assert client.get("/retrieval").status_code==200
    ledger=[{"doc_id":"0090","rebuild_state":"PENDING","scope_epoch":"e1","ledger_version":"v1"}]
    api=client.post("/api/retrieval",json={"sample":"dirty.csv","parameters":{"migration_ledger":ledger}}).get_json()
    by={r["doc_id"]:r for r in api["rows"]}
    assert by["0090"]["retrieval_route"]=="LEGACY_FALLBACK"
    assert by["0001"]["retrieval_route"]=="SUPPRESSED"
