"""The "My mistakes" window: the report from chesscoach.report, with clickable examples."""

from __future__ import annotations

import html

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..classify import Judgement
from ..db import DatabaseError, MistakeDB
from ..report import Filters, Period, Report, ReportMistake, TimeClass, build_report
from . import style

# Example links: "mistake:<id>" opens the puzzle in the app.
MISTAKE_SCHEME = "mistake"


def _e(text: object) -> str:
    return html.escape(str(text))


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"1 {word}"
    return f"{n} {word[:-1]}ies" if word.endswith("y") else f"{n} {word}s"


def _judgement(name: str) -> str:
    color = style.judgement_color(Judgement(name))
    return f"<span style='color:{color.name()}'>{_e(name)}</span>" if color else _e(name)


def _table(headers: list[str], rows: list[list[str]], text_columns=(0,)) -> str:
    """A table with numbers right-aligned and the ``text_columns`` left-aligned."""

    def align(i: int) -> str:
        return "left" if i in text_columns else "right"

    head = "".join(f"<th align='{align(i)}'>{h}</th>" for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f"<td align='{align(i)}'>{c}</td>" for i, c in enumerate(row)) + "</tr>"
        for row in rows
    )
    return f"<table cellspacing='0' cellpadding='4'><tr>{head}</tr>{body}</table>"


def _example(m: ReportMistake) -> str:
    date = m.date.isoformat() if m.date else "?"
    text = (
        f"{_e(date)} · vs {_e(m.opponent)} · <b>{_e(m.move_label)}</b> · "
        f"{_judgement(m.judgement)} −{m.win_loss:.0f}%"
    )
    tags = [t.label for t in m.visible_tags]
    if tags:
        text += f" · <i>{_e(', '.join(tags))}</i>"
    if m.has_pgn:
        text += f" · <a href='{MISTAKE_SCHEME}:{m.id}'>Retry</a>"
    elif m.link:
        text += f" · <a href='{_e(m.link)}'>Game</a>"
    return f"<li>{text}</li>"


def report_html(report: Report) -> str:
    """The report as HTML. Never contains a best move; missed tags only for seen answers."""
    if not report.games:
        if report.filters != Filters():
            return "<p>No games match these filters.</p>"
        return (
            "<p>No games in your mistake database yet. Analyze some games first: "
            "open them here, or run <code>chesscoach-cli batch &lt;username&gt;</code>.</p>"
        )
    parts: list[str] = []
    total = len(report.mistakes)
    j = report.by_judgement
    span = f", {report.first_date} to {report.last_date}" if report.first_date else ""
    parts.append(
        f"<h2>My top mistakes</h2><p>{_plural(report.games, 'game')}{_e(span)}: "
        f"<b>{_plural(total, 'mistake')}</b> ({report.per_game:.1f} per game) — "
        f"{_judgement('blunder')} {j['blunder']}, {_judgement('mistake')} {j['mistake']}, "
        f"{_judgement('inaccuracy')} {j['inaccuracy']}.</p>"
    )
    if total:
        parts.append(
            f"<p>{report.no_pattern} of them ({100 * report.no_pattern / total:.0f}%) match no "
            "pattern: quiet positional or endgame errors. The phase table shows where they happen.</p>"
        )

    if report.tags:
        rows = [
            [_e(r.tag.label), str(r.count), f"{r.share:.0f}%", f"{r.avg_loss:.0f}%", f"{r.done}/{r.count}"]
            for r in report.tags
        ]
        parts.append("<h3>By pattern</h3>")
        parts.append(_table(["Pattern", "Count", "Share", "Avg loss", "Done"], rows))
        parts.append("<p><small>Done: solved or revealed in a retry.</small></p>")

    rows = [
        [
            _e(r.phase.label), str(r.count), str(r.by_judgement["blunder"]),
            str(r.by_judgement["mistake"]), str(r.by_judgement["inaccuracy"]),
            str(r.no_pattern), f"{r.avg_loss:.0f}%",
        ]
        for r in report.phases
    ]
    parts.append("<h3>By phase</h3>")
    parts.append(_table(["Phase", "Count", "Blunders", "Mistakes", "Inacc.", "No pattern", "Avg loss"], rows))

    rows = [
        [_e(r.time_class.label), str(r.games), f"{r.per_game:.1f}", f"{r.blunders_per_game:.1f}"]
        for r in report.time_classes
    ]
    parts.append("<h3>By time control</h3>")
    parts.append(_table(["Time control", "Games", "Mistakes / game", "Blunders / game"], rows))

    if report.trend:
        rows = [
            [
                _e(r.period), str(r.games), f"{r.per_game:.1f}", f"{r.blunders_per_game:.1f}",
                _e(", ".join(f"{t.label} {n}" for t, n in r.top_patterns) or "–"),
            ]
            for r in report.trend
        ]
        parts.append(f"<h3>Trend by {report.period.value}</h3>")
        headers = ["Period", "Games", "Mistakes / game", "Blunders / game", "Top patterns"]
        parts.append(_table(headers, rows, text_columns=(0, 4)))

    parts.append("<h3>Examples</h3><p><small>Biggest drop in win % first. "
                 "Retry opens the position as a puzzle; the best move stays hidden.</small></p>")
    for r in report.tags:
        parts.append(f"<h4>{_e(r.tag.label)}</h4><ul>{''.join(_example(m) for m in r.examples)}")
        if r.hidden:
            parts.append(
                f"<li><i>{_plural(r.hidden, 'position')} not listed: listing them here would hint "
                "at the answer. Retry your mistakes to unlock them.</i></li>"
            )
        parts.append("</ul>")
    for r in report.phases:
        if r.no_pattern_examples:
            parts.append(
                f"<h4>No pattern found: {_e(r.phase.label.lower())}</h4>"
                f"<ul>{''.join(_example(m) for m in r.no_pattern_examples)}</ul>"
            )
    return "".join(parts)


class ReportDialog(QDialog):
    """Shows the report; emits ``open_mistake(id)`` when an example's Retry link is clicked."""

    open_mistake = Signal(int)

    def __init__(self, db: MistakeDB, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("My mistakes")
        self.resize(820, 720)
        self.db = db

        self.time_control = QComboBox()
        self.time_control.addItem("All", None)
        for tc in TimeClass:
            if tc is not TimeClass.UNKNOWN:
                self.time_control.addItem(tc.label, tc)
        self.last_games = QSpinBox()
        self.last_games.setRange(0, 10_000)
        self.last_games.setSingleStep(10)
        self.last_games.setSpecialValueText("all")
        self.period = QComboBox()
        for p in Period:
            self.period.addItem(p.value.capitalize(), p)

        filters = QHBoxLayout()
        for label, widget in (
            ("Time control", self.time_control),
            ("Last games", self.last_games),
            ("Trend by", self.period),
        ):
            filters.addWidget(QLabel(label))
            filters.addWidget(widget)
        filters.addStretch(1)

        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._link_clicked)

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self.browser)

        self.time_control.currentIndexChanged.connect(self.refresh)
        self.last_games.valueChanged.connect(self.refresh)
        self.period.currentIndexChanged.connect(self.refresh)

    def filters(self) -> Filters:
        return Filters(
            last_games=self.last_games.value() or None,
            time_class=self.time_control.currentData(),
        )

    def refresh(self) -> None:
        try:
            report = build_report(self.db, self.filters(), period=self.period.currentData())
            if report.stale_tags:  # tagged by older rules: quick to redo from what is stored
                self.db.retag()
                report = build_report(self.db, self.filters(), period=self.period.currentData())
        except DatabaseError as exc:
            self.browser.setHtml(f"<p>Could not read your mistake database: {_e(exc)}</p>")
            return
        scroll = self.browser.verticalScrollBar().value()
        self.browser.setHtml(report_html(report))
        self.browser.verticalScrollBar().setValue(scroll)

    def _link_clicked(self, url: QUrl) -> None:
        if url.scheme() == MISTAKE_SCHEME:
            try:
                mistake_id = int(url.path())
            except ValueError:
                return
            self.hide()  # the puzzle needs the board; Ctrl+R brings the report back
            self.open_mistake.emit(mistake_id)
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
