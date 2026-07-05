from pathlib import Path

from scripts import n8n_stage_a_smoke


def test_find_audit_record_reads_from_offset(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        '{"lead_id":"old","mode":"simulation","sent":false,"simulated":true,"status":"simulated"}\n',
        encoding="utf-8",
    )
    offset = audit.stat().st_size
    audit.write_text(
        audit.read_text(encoding="utf-8")
        + '{"lead_id":"target","mode":"simulation","sent":false,"simulated":true,"status":"simulated"}\n',
        encoding="utf-8",
    )

    record = n8n_stage_a_smoke.find_audit_record(audit, "target", start_offset=offset)

    assert record == {
        "lead_id": "target",
        "mode": "simulation",
        "sent": False,
        "simulated": True,
        "status": "simulated",
    }
    assert n8n_stage_a_smoke.find_audit_record(audit, "old", start_offset=offset) is None


def test_latest_n8n_execution_returns_successful_workflow(tmp_path: Path):
    db = tmp_path / "database.sqlite"
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            create table workflow_entity (id text primary key, name text not null);
            create table execution_entity (
                id integer primary key autoincrement,
                workflowId text not null,
                mode text not null,
                status text not null,
                startedAt text,
                stoppedAt text
            );
            insert into workflow_entity (id, name) values ('wf1', 'Agent-core Stage A inbound simulation');
            insert into execution_entity (workflowId, mode, status, startedAt, stoppedAt)
            values ('wf1', 'webhook', 'success', '2026-07-05 03:07:12.436', '2026-07-05 03:07:12.536');
            """
        )

    execution = n8n_stage_a_smoke.latest_n8n_execution(db)

    assert execution is not None
    assert execution["workflowId"] == "wf1"
    assert execution["mode"] == "webhook"
    assert execution["status"] == "success"
