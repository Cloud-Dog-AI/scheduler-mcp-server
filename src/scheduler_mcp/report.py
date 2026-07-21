"""Run report rendering — W28K-1409 F-1409-6.

Renders a scheduler run (summary + chain step trace + audit lifecycle) to a
self-contained PDF via reportlab's platypus flowables (the chart-mcp W28F-908/910
pattern). Pure rendering: callers gather the run DTO, step rows, and audit events
and pass them in; this module has no DB/IDAM/network dependency so it is unit
testable and the API layer owns auth + data access.
"""

from __future__ import annotations

import io
from typing import Any


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def render_run_report_pdf(
    run: dict[str, Any],
    steps: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
    *,
    title: str | None = None,
) -> bytes:
    """Render a run report PDF and return the raw bytes.

    `run` is the /v1/runs/{id} DTO; `steps` are the chain step-run rows (may be
    empty for a non-chain run); `audit_events` are the lifecycle audit rows
    (correlation_id == run id), oldest first.
    """
    # Imported lazily so importing this module never hard-requires reportlab in
    # environments that only touch other surfaces.
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("RH1", parent=styles["Heading1"], fontSize=16, spaceAfter=6)
    h2 = ParagraphStyle("RH2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("RBody", parent=styles["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("RSmall", parent=styles["BodyText"], fontSize=7, leading=9)

    run_id = _s(run.get("schedule_run_id") or run.get("run_id"))
    flow: list[Any] = []
    flow.append(Paragraph(title or f"Scheduler Run Report — {run_id}", h1))
    flow.append(Paragraph(f"schedule_id: {_s(run.get('schedule_id'))} · status: <b>{_s(run.get('status'))}</b>", body))
    flow.append(Spacer(1, 4 * mm))

    # --- Run summary table -------------------------------------------------
    flow.append(Paragraph("Run summary", h2))
    summary_fields = [
        ("schedule_run_id", run_id),
        ("schedule_id", _s(run.get("schedule_id"))),
        ("tenant_id", _s(run.get("tenant_id"))),
        ("status", _s(run.get("status"))),
        ("trigger_type", _s(run.get("trigger_type"))),
        ("attempt", _s(run.get("attempt"))),
        ("scheduled_for", _s(run.get("scheduled_for"))),
        ("started_at", _s(run.get("started_at"))),
        ("finished_at", _s(run.get("finished_at"))),
        ("error_code", _s(run.get("error_code"))),
        ("error_summary", _s(run.get("error_summary"))),
        ("chain_run_id", _s(run.get("chain_run_id"))),
        ("result_ref", _s(run.get("result_ref"))),
    ]
    st = Table(
        [[Paragraph(f"<b>{k}</b>", small), Paragraph(v or "—", small)] for k, v in summary_fields],
        colWidths=[40 * mm, 130 * mm],
    )
    st.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
            ]
        )
    )
    flow.append(st)

    # --- Step trace table --------------------------------------------------
    flow.append(Paragraph(f"Step trace ({len(steps)} step(s))", h2))
    if steps:
        head = ["#", "step_id", "step_type", "status", "started_at", "finished_at"]
        rows = [[Paragraph(f"<b>{h}</b>", small) for h in head]]
        for i, s in enumerate(steps, 1):
            rows.append(
                [
                    Paragraph(str(i), small),
                    Paragraph(_s(s.get("step_id")), small),
                    Paragraph(_s(s.get("step_type")), small),
                    Paragraph(_s(s.get("status")), small),
                    Paragraph(_s(s.get("started_at")), small),
                    Paragraph(_s(s.get("finished_at")), small),
                ]
            )
        tt = Table(rows, colWidths=[8 * mm, 34 * mm, 30 * mm, 24 * mm, 37 * mm, 37 * mm])
        tt.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        flow.append(tt)
    else:
        flow.append(Paragraph("No chain steps (single-target run).", body))

    # --- Audit lifecycle ---------------------------------------------------
    flow.append(Paragraph(f"Audit lifecycle ({len(audit_events)} event(s))", h2))
    if audit_events:
        head = ["timestamp", "action / event", "outcome", "actor"]
        rows = [[Paragraph(f"<b>{h}</b>", small) for h in head]]
        for e in audit_events:
            actor = e.get("actor") or {}
            actor_s = (
                actor.get("actor_id") or actor.get("username") or actor.get("id")
                if isinstance(actor, dict)
                else _s(actor)
            )
            rows.append(
                [
                    Paragraph(_s(e.get("timestamp")), small),
                    Paragraph(_s(e.get("action") or e.get("event_type")), small),
                    Paragraph(_s(e.get("outcome")), small),
                    Paragraph(_s(actor_s), small),
                ]
            )
        at = Table(rows, colWidths=[48 * mm, 52 * mm, 28 * mm, 42 * mm])
        at.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        flow.append(at)
    else:
        flow.append(Paragraph("No audit events recorded for this run.", body))

    flow.append(Spacer(1, 6 * mm))
    flow.append(Paragraph("Generated by scheduler-mcp-server /v1/runs/{id}/report — W28K-1409 F-1409-6.", small))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Scheduler Run Report {run_id}",
    )
    doc.build(flow)
    return buf.getvalue()
