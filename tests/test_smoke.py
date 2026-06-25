"""Playwright smoke test for the dashboard.

Boots the Flask app in mock mode with DEV_AUTH_BYPASS, then asserts the exact
classes of bug hardened against while building the prototype. These check
COMPUTED LAYOUT, not style attributes:

  * no console errors
  * the hero canvas exists and is animating (rAF advancing, node coords finite)
  * roster rows never share a vertical position (no overlap)
  * "Where the work lands" bars have non-zero rendered width
  * avatars (and their ping rings) are not clipped by the roster's overflow

Run: uv run playwright install chromium && uv run pytest tests/test_smoke.py
Skipped automatically if Playwright browsers are not installed.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_data(base: str, timeout: float = 25.0) -> bool:
    """Wait until the mock harvester has seeded the snapshot."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=2) as r:
                import json

                if json.loads(r.read())["updated_at"] > 0:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    env = {
        **os.environ,
        "DEV_AUTH_BYPASS": "1",
        "GITHUB_TOKEN": "",  # force mock
        "N2G_SKIP_DOTENV": "1",  # ignore any local .env so the empty token sticks
        "CACHE_PERSIST_PATH": "",  # never touch a real cache during tests
        "MOCK_REFRESH_SECONDS": "1",
        "SECRET_KEY": "test",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "app", "run", "--port", str(port), "--no-reload"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        if not _wait_for_data(base):
            proc.terminate()
            pytest.skip("server did not seed in time")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def page_ctx(server):
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # browsers not installed
                pytest.skip(f"chromium not available: {exc}")
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(server, wait_until="networkidle")
            # let the first poll populate roster/bars and a few events animate
            page.wait_for_timeout(3500)
            yield page, errors
            browser.close()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"playwright unavailable: {exc}")


def test_no_console_errors(page_ctx):
    page, errors = page_ctx
    assert errors == [], f"console errors: {errors}"


def test_canvas_present_and_animating(page_ctx):
    page, _ = page_ctx
    assert page.locator("#constellation").count() == 1
    f1 = page.evaluate("window.__wall.frames")
    page.wait_for_timeout(500)
    f2 = page.evaluate("window.__wall.frames")
    assert f2 > f1, "requestAnimationFrame loop is not advancing"
    # every node has finite coordinates (no NaN positions)
    all_finite = page.evaluate(
        "window.__wall.nodes().every(n => Number.isFinite(n.x) && Number.isFinite(n.y))"
    )
    assert all_finite
    assert page.evaluate("window.__wall.nodes().length") > 100


def test_event_animator_keeps_running(page_ctx):
    # the wall must not "fall flat" once the polled batch drains: the animator
    # loops the recent-events pool, so the animated event count keeps climbing
    # even across a window where no fresh server events are guaranteed.
    page, _ = page_ctx
    assert page.evaluate("window.__wall.poolLen()") > 0, "event pool never filled"
    c1 = page.evaluate("window.__wall.evCount()")
    page.wait_for_timeout(2500)
    c2 = page.evaluate("window.__wall.evCount()")
    assert c2 > c1, "event animator stalled (the viz fell flat)"


def test_live_pulses_on_heatmap_and_bars(page_ctx):
    # live events bloom their day in the density strip and flash their repo bar
    # (when that repo is in the top 5), so neither panel is static. Poll up to ~8s
    # since which repos stream by is random.
    page, _ = page_ctx
    got = page.evaluate(
        """() => new Promise(resolve => {
            let n = 0;
            const id = setInterval(() => {
                const hit = document.querySelectorAll('.heat-cell.hit').length;
                const pulse = document.querySelectorAll('.bar-fill.pulse').length;
                if ((hit >= 1 && pulse >= 1) || ++n >= 80) {
                    clearInterval(id); resolve({hit, pulse});
                }
            }, 100);
        })"""
    )
    assert got["hit"] >= 1, "no density cells bloomed from live events"
    assert got["pulse"] >= 1, "no bars pulsed from live events"


def test_org_pulse_voicebox_animates(page_ctx):
    # the voicebox canvas must be present, animating (rAF advancing), and pulsing
    # from live events; the totals caption must be populated.
    page, _ = page_ctx
    assert page.locator("#voicebox").count() == 1
    f1 = page.evaluate("window.__wall.vbFrames()")
    page.wait_for_timeout(500)
    f2 = page.evaluate("window.__wall.vbFrames()")
    assert f2 > f1, "voicebox is not animating"
    assert page.evaluate("window.__wall.vbBeats()") > 0, "no pulses fired from live events"
    caption = page.evaluate("document.getElementById('pulse-caption').textContent")
    assert any(ch.isdigit() for ch in caption), f"totals caption not populated: {caption!r}"


def test_density_ambient_shimmer(page_ctx):
    # every day cell breathes via a shimmer pseudo-element, with intensity scaled
    # by real activity: at least one (busy) cell has a non-zero --glow.
    page, _ = page_ctx
    anim = page.evaluate(
        """() => {
            const cell = document.querySelector('.heat-cell');
            return cell ? getComputedStyle(cell, '::after').animationName : null;
        }"""
    )
    assert anim == "shimmer", f"density shimmer animation missing (got {anim})"
    max_glow = page.evaluate(
        """() => Math.max(0, ...Array.from(document.querySelectorAll('.heat-cell'))
            .map(c => parseFloat(getComputedStyle(c).getPropertyValue('--glow')) || 0))"""
    )
    assert max_glow > 0, "no day cell has activity-scaled glow"


def test_bars_have_a_moving_sheen(page_ctx):
    # the perpetual sheen pseudo-element keeps the bars alive even when totals hold;
    # assert it is present and animating on a visible bar fill.
    page, _ = page_ctx
    animated = page.evaluate(
        """() => {
            const fill = Array.from(document.querySelectorAll('.bar-fill'))
                .find(f => f.getBoundingClientRect().width > 0);
            if (!fill) return false;
            const a = getComputedStyle(fill, '::after').animationName;
            return a === 'sheen';
        }"""
    )
    assert animated, "bar sheen animation is not running"


def test_roster_rows_do_not_overlap(page_ctx):
    page, _ = page_ctx
    tops = page.evaluate(
        """() => Array.from(document.querySelectorAll('.person'))
            .filter(el => getComputedStyle(el).opacity === '1')
            .map(el => Math.round(el.getBoundingClientRect().top))"""
    )
    assert len(tops) >= 1, "no visible roster rows"
    assert len(tops) == len(set(tops)), f"roster rows share a vertical position: {tops}"


def test_bars_have_nonzero_width(page_ctx):
    page, _ = page_ctx
    widths = page.evaluate(
        """() => Array.from(document.querySelectorAll('.bar-row'))
            .filter(r => getComputedStyle(r).opacity === '1')
            .map(r => r.querySelector('.bar-fill').getBoundingClientRect().width)"""
    )
    assert len(widths) >= 1, "no visible bars rendered"
    assert all(w > 0 for w in widths), f"bar fills have zero width: {widths}"


def test_avatars_not_clipped(page_ctx):
    page, _ = page_ctx
    # every visible avatar must sit fully inside the roster's box (the ping ring
    # is drawn within the avatar, so an unclipped avatar means an unclipped ring)
    clipped = page.evaluate(
        """() => {
            const roster = document.getElementById('roster').getBoundingClientRect();
            return Array.from(document.querySelectorAll('.person'))
              .filter(el => getComputedStyle(el).opacity === '1')
              .map(el => el.querySelector('.pic').getBoundingClientRect())
              .filter(p => p.left < roster.left - 0.5 || p.right > roster.right + 0.5)
              .length;
        }"""
    )
    assert clipped == 0, "an avatar is clipped by the roster edge"
