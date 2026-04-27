#!/usr/bin/env python3
"""
Claude Token Meter — macOS menu bar usage tracker
"""

import objc
import json
import glob
import os
import sys
import fcntl
from datetime import datetime, timedelta

# ── Single-instance lock ──────────────────────────────────────────────────────
_LOCK_FILE = "/tmp/claude_token_meter.lock"
_lock_fd = open(_LOCK_FILE, "w")
try:
    fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    print("Claude Token Meter is already running — exiting duplicate.")
    sys.exit(0)

import AppKit
from AppKit import (
    NSApplication, NSStatusBar, NSVariableStatusItemLength,
    NSColor, NSFont, NSAttributedString, NSImage,
    NSFontAttributeName, NSForegroundColorAttributeName,
    NSApplicationActivationPolicyAccessory,
    NSPopover, NSViewController, NSView,
    NSVisualEffectView, NSMenu, NSMenuItem,
)
from Foundation import (
    NSObject, NSTimer, NSRunLoop, NSDefaultRunLoopMode,
    NSURL, NSMakeRect, NSMakeSize, NSMakeRange, NSMakePoint,
)
from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController

NSRectEdgeMinY = 1   # show popover below the menu bar button
NSVisualEffectBlendingModeBehindWindow = 0
NSVisualEffectStateActive = 1
NSVisualEffectMaterialPopover = 6

from meter_core import (
    SETTINGS_FILE, PROJECTS_GLOB,
    DEFAULT_LIMIT, SESSION_LIMIT, WEEK_LIMIT,
    load_settings, save_settings,
    fetch_claude_ai_usage, build_payload,
    get_usage, detect_session_start,
)

POPOVER_W  = 300
POPOVER_H  = 593   # base: efficiency collapsed by default (-70 body)
POPOVER_H_COOKIE = 868   # WKWebView ceiling: all panels open

# ─── HTML / CSS / JS ──────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en" data-theme="system">
<head>
<meta charset="UTF-8">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --g1: #091828; --g2: #0F2844; --g3: #163A60;
  --card:   rgba(255,255,255,0.10);
  --card-b: rgba(255,255,255,0.14);
  --t1: rgba(255,255,255,0.96);
  --t2: rgba(255,255,255,0.52);
  --t3: rgba(255,255,255,0.30);
  --div: rgba(255,255,255,0.10);
  --track: rgba(255,255,255,0.10);
  --arc-fill: rgba(255,245,215,0.92);
  --dot-fill: #ffffff;
  --pill: rgba(255,255,255,0.10);
  --pill-act-bg: rgba(255,255,255,0.18);
  --pill-act-border: rgba(255,255,255,0.40);
  --pill-act-color: rgba(255,255,255,1.0);
  --bar-idle: rgba(255,255,255,0.14);
  --bar-today: rgba(255,245,215,0.88);
  --theme-active: rgba(255,255,255,0.22);
  --grad-start: rgba(82,186,255,0.92);
  --grad-mid:   rgba(168,124,245,0.90);
  --grad-end:   rgba(242,98,128,0.92);
  --tick: rgba(255,255,255,1);
}

html[data-theme="light"] {
  --g1: #E4E4E8; --g2: #EBEBEF; --g3: #F2F2F5;
  --card:   rgba(255,255,255,0.78);
  --card-b: rgba(255,255,255,0.95);
  --tick: rgba(10,20,40,1);
  --t1: rgba(10,20,40,0.95);
  --t2: rgba(10,20,40,0.68);
  --t3: rgba(10,20,40,0.48);
  --div: rgba(0,0,0,0.10);
  --track: rgba(0,0,0,0.20);
  --arc-fill: rgba(20,60,120,0.85);
  --dot-fill: #0D2C60;
  --pill: rgba(0,0,0,0.08);
  --pill-act-bg: rgba(0,0,0,0.16);
  --pill-act-border: rgba(0,0,0,0.30);
  --pill-act-color: rgba(10,20,40,1.0);
  --bar-idle: rgba(0,0,0,0.14);
  --bar-today: rgba(20,60,120,0.80);
  --theme-active: rgba(0,0,0,0.15);
  --grad-start: rgba(28,108,210,0.95);
  --grad-mid:   rgba(118,46,196,0.92);
  --grad-end:   rgba(196,28,72,0.95);
}

html, body {
  width: 300px; height: auto;
  overflow: hidden; background: var(--g1);
  text-align: left;
}
body {
  font-family: -apple-system, 'SF Pro Text', 'Helvetica Neue', sans-serif;
  color: var(--t1);
  -webkit-font-smoothing: antialiased;
  text-align: left;
}

#app {
  width: 300px; height: 593px;
  background: linear-gradient(175deg, var(--g1) 0%, var(--g2) 50%, var(--g3) 100%);
  border-radius: 14px;
  display: flex; flex-direction: column;
  overflow: hidden;
  transition: background 0.8s ease;
  position: relative;
}

/* Subtle noise grain */
#app::after {
  content: '';
  position: absolute; inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");
  opacity: 0.025; pointer-events: none; border-radius: 14px;
}

/* ── Header ── */
.hdr {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 18px 0;
}
.hdr-title {
  font-size: 10px; font-weight: 600;
  letter-spacing: 1.8px; text-transform: uppercase;
  color: var(--t2);
}
.hdr-month {
  font-size: 10px; font-weight: 500;
  letter-spacing: 0.6px; color: var(--t3);
}

/* ── Hero ── */
.hero {
  display: flex; flex-direction: column;
  align-items: center;
  padding: 10px 0 0;
}
.hero-pct {
  font-family: -apple-system, 'SF Pro Display', 'Helvetica Neue', sans-serif;
  font-size: 58px; font-weight: 200;
  letter-spacing: -2px; line-height: 1;
  color: var(--t1);
  transition: color 0.5s;
}
.hero-status {
  font-size: 13px; font-weight: 400;
  color: var(--t2); margin-top: 3px;
  letter-spacing: 0.2px;
}

/* ── Arc gauge ── */
.arc-wrap {
  display: flex; flex-direction: column;
  align-items: center;
  padding: 2px 0 4px;
  position: relative;
}
.arc-svg { display: block; overflow: visible; }
.token-sub {
  font-size: 11px; font-weight: 400;
  color: var(--t2); letter-spacing: 0.2px;
  font-variant-numeric: tabular-nums;
  margin-top: 2px;
}

/* ── Cards ── */
.card {
  margin: 8px 14px 0;
  background: var(--card);
  border: 1px solid var(--card-b);
  border-radius: 12px;
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  overflow: hidden;
  position: relative;
}

/* ── Shimmer overlay (card open animation) ── */
.shimmer-overlay {
  position: absolute; inset: 0; border-radius: 12px;
  pointer-events: none; opacity: 0;
  background: linear-gradient(90deg,
    transparent 0%, rgba(255,255,255,0.09) 50%, transparent 100%);
  background-size: 200% 100%;
  background-position: -100% 0;
}
@keyframes shimmerSlide {
  0%   { background-position: -100% 0; opacity: 1; }
  100% { background-position: 220% 0;  opacity: 0; }
}
.shimmer-overlay.run { animation: shimmerSlide 0.85s ease-out forwards; }

/* ── Carbon gauge show/hide ── */
.gauge-carbon { display: none; }
html[data-vivid="on"] .gauge-default { display: none; }
html[data-vivid="on"] .gauge-carbon  { display: block; }

/* ── Arc glow ── */
@keyframes arcGlow {
  0%, 100% { filter: drop-shadow(0 0 2px rgba(168,124,245,0.30)); }
  50%       { filter: drop-shadow(0 0 7px rgba(168,124,245,0.65)); }
}
.arc-glow { animation: arcGlow 3s ease-in-out infinite; }

/* ── Gauge idle pulse ── */
@keyframes idlePulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.55; }
}
.idle-pulse { animation: idlePulse 3.5s ease-in-out infinite; }

/* ── Number pop (after count-up lands) ── */
@keyframes numPop {
  0%   { transform: scale(1); }
  55%  { transform: scale(1.08); }
  100% { transform: scale(1); }
}
.num-pop {
  animation: numPop 0.38s cubic-bezier(0.34,1.56,0.64,1);
  transform-box: fill-box; transform-origin: center;
  display: inline-block;
}

/* Stats card */
.stats {
  display: grid; grid-template-columns: 1fr 1px 1fr;
  flex-shrink: 0;
}
.stat-div { background: var(--div); }
.stat {
  display: flex; flex-direction: column;
  align-items: center; padding: 16px 8px 16px; gap: 4px;
}
.stat-lbl {
  font-size: 8px; font-weight: 600;
  letter-spacing: 1.4px; color: var(--t3);
  text-transform: uppercase; margin-bottom: 2px;
}
.stat-val {
  font-family: 'SF Mono', ui-monospace, Menlo, monospace;
  font-size: 22px; font-weight: 500;
  color: var(--t1); letter-spacing: -0.5px;
  line-height: 1.1;
}
.pct-sign {
  font-size: 11px; vertical-align: text-top; position: relative; top: 3px;
}
.stat-cd {
  font-family: 'SF Mono', ui-monospace, Menlo, monospace;
  font-size: 11px; font-weight: 400;
  color: var(--t2); letter-spacing: -0.2px;
  margin-top: 2px;
}
.stat-reset {
  font-size: 8.5px; font-weight: 400;
  color: var(--t3); letter-spacing: 0.1px;
  margin-top: 1px;
}

/* ── Efficiency card ───────────────────────────────────────────────────── */
.eff-card { padding: 12px 14px 12px; }
.eff-hdr {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 0; cursor: pointer;
}
.eff-hdr-left { display: flex; align-items: center; gap: 5px; }
.eff-hdr-left .stat-lbl { margin-bottom: 0; }
.eff-info-btn {
  background: none; border: none; cursor: pointer; padding: 0;
  color: var(--t3); display: flex; align-items: center;
  transition: color 0.15s; -webkit-appearance: none; flex-shrink: 0;
  line-height: 1;
}
.eff-info-btn:hover { color: var(--t1); }
/* Tooltip — fixed so card overflow:hidden never clips it */
.eff-tooltip {
  position: fixed;
  width: 200px;
  background: rgba(16,32,62,0.97);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 12px;
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  padding: 10px 12px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  pointer-events: none; opacity: 0;
  transition: opacity 0.18s ease, transform 0.18s ease;
  transform: translateY(-4px);
  z-index: 999;
}
.eff-tooltip.visible {
  pointer-events: auto; opacity: 1;
  transform: translateY(0);
}
/* upward-pointing arrow */
/* downward-pointing arrow (tooltip opens above) */
.eff-tooltip::before {
  content: ''; position: absolute;
  top: 100%; left: 14px;
  border: 5px solid transparent;
  border-top-color: rgba(255,255,255,0.18);
}
.eff-tooltip::after {
  content: ''; position: absolute;
  top: calc(100% - 1px); left: 14px;
  border: 5px solid transparent;
  border-top-color: rgba(16,32,62,0.97);
  z-index: 1;
}
.eff-tip-title {
  font-size: 10px; font-weight: 600;
  color: rgba(255,255,255,0.9);
  letter-spacing: 0.3px; margin-bottom: 5px;
}
.eff-tip-body {
  font-size: 9.5px; color: rgba(255,255,255,0.65);
  line-height: 1.5;
}
.eff-tip-body b { color: rgba(255,255,255,0.85); }
.eff-tooltip .tip-row { margin-top: 5px; display: flex; gap: 6px; align-items: flex-start; }
.eff-tooltip .tip-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; margin-top: 3px; }
/* Carbon mode — arc animations */
@keyframes arcPulseLow  { 0%,100% { stroke-width: 4; stroke-opacity: 0.80; } 50% { stroke-width: 6.5; stroke-opacity: 1; } }
@keyframes arcPulseMed  { 0%,100% { stroke-width: 4; stroke-opacity: 0.80; } 50% { stroke-width: 6.5; stroke-opacity: 1; } }
@keyframes arcPulseHigh { 0%,100% { stroke-width: 4; stroke-opacity: 0.80; } 50% { stroke-width: 6.5; stroke-opacity: 1; } }
.arc-pulse-low  { animation: arcPulseLow  2.4s ease-in-out infinite; }
.arc-pulse-med  { animation: arcPulseMed  2.4s ease-in-out infinite; }
.arc-pulse-high { animation: arcPulseHigh 2.4s ease-in-out infinite; }
/* Carbon light-up entry: arcs start invisible, JS fades them in sequentially */
.arc-entry { opacity: 0; transition: opacity 1.1s ease; }
/* Carbon mode tooltip overrides */
html[data-vivid="on"] .eff-tooltip {
  background: rgba(38,40,50,0.97);
  border-color: rgba(255,255,255,0.10);
}
html[data-vivid="on"] .eff-tooltip::after {
  border-top-color: rgba(38,40,50,0.97);
}
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(1) .tip-dot { background: #AAD7FE !important; }
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(2) .tip-dot { background: #98CECC !important; }
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(3) .tip-dot { background: #ECB967 !important; }
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(4) .tip-dot { background: #F09060 !important; }
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(5) .tip-dot { background: #FF654D !important; }
/* Carbon — cookie expired tooltip + info panel match eff-tooltip background */
html[data-vivid="on"] .cookie-warn-tip {
  background: rgba(38,40,50,0.97);
  border-color: rgba(255,160,50,0.4);
}
html[data-vivid="on"] .cookie-warn-tip::before {
  border-top-color: rgba(38,40,50,0.97);
}
html[data-vivid="on"] .info-panel {
  background: rgba(38,40,50,0.97);
  border-color: rgba(255,255,255,0.10);
}
/* Carbon — "THIS SESSION" label slightly brighter for contrast */
html[data-vivid="on"] #gaugeSessionLbl { opacity: 0.88; }
.eff-toggle {
  background: none; border: none; cursor: pointer; padding: 2px;
  color: var(--t3); display: flex; align-items: center;
  transition: color 0.15s; -webkit-appearance: none; flex-shrink: 0;
}
.eff-toggle:hover { color: var(--t1); }
.eff-toggle svg { transition: transform 0.28s ease; }
.eff-toggle.open svg { transform: rotate(180deg); }
#effBody {
  overflow: hidden;
  padding: 10px 0 0;
  transition: max-height 0.28s ease, opacity 0.22s ease, padding 0.28s ease;
}
#effBody.open  { opacity: 1; }
#effBody.shut  { max-height: 0 !important; opacity: 0; padding-top: 0; }
.eff-segs { display: flex; gap: 3px; height: 5px; margin-bottom: 5px; }
.eff-seg  { flex: 1; border-radius: 2px; opacity: 0.18; transition: opacity 0.35s ease, transform 0.35s ease; }
.eff-seg.on { opacity: 1; }
@keyframes seg-cascade-active {
  0%   { opacity: 0;    transform: scaleY(0.4); }
  55%  { opacity: 1;    transform: scaleY(1.12); }
  100% { opacity: 1;    transform: scaleY(1); }
}
@keyframes seg-cascade-dim {
  0%   { opacity: 0;    transform: scaleY(0.4); }
  55%  { opacity: 1;    transform: scaleY(1.12); }
  100% { opacity: 0.18; transform: scaleY(1); }
}
@keyframes seg-zone-activate {
  0%   { transform: scale(1); }
  40%  { transform: scaleY(1.35) scaleX(1.04); }
  100% { transform: scale(1); }
}
.eff-lbls { display: flex; margin-bottom: 8px; }
.eff-lbl  {
  flex: 1; font-size: 7.5px; color: var(--t3);
  text-align: center; opacity: 0.45;
  transition: opacity 0.4s, color 0.4s; letter-spacing: 0.2px;
}
.eff-lbl.on { opacity: 1; color: var(--t1); }
.eff-meta {
  font-size: 9px; color: var(--t3); text-align: center;
  letter-spacing: 0.3px; margin-bottom: 10px;
}
.eff-hist { display: flex; padding-top: 9px; border-top: 1px solid var(--div); }
.eff-hist-col { flex: 1; text-align: center; }
.eff-hist-val { font-size: 10.5px; font-weight: 400; margin-top: 3px; letter-spacing: 0.2px; }
.eff-hist-sep { width: 1px; background: var(--div); }

/* ── 7-Day Usage chart ──────────────────────────────────────────────────── */
.week-card { padding: 12px 14px 12px; }
.week-hdr  { display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
#weekChartBody {
  overflow: hidden; padding: 10px 0 0;
  transition: max-height 0.28s ease, opacity 0.22s ease, padding 0.28s ease;
}
#weekChartBody.open { opacity: 1; }
#weekChartBody.shut { max-height: 0 !important; opacity: 0; padding-top: 0; }
.week-chart {
  display: flex; gap: 4px;
  height: 80px;   /* 10 segs × 6px + 9 gaps × 2px = 78px, +2 breathing room */
}
.week-bar-col {
  flex: 1; display: flex; flex-direction: column; gap: 2px;
}
/* Each segment is a horizontal pill — the stack builds the bar */
.wseg {
  flex: 1; border-radius: 3px;
  transition: opacity 0.22s ease;
}
.wseg.wdim { opacity: 0.10; }
.week-labels {
  display: flex; gap: 4px; padding-top: 6px; height: 17px;
}
.week-lbl {
  flex: 1; text-align: center;
  font-size: 7.5px; color: var(--t3); letter-spacing: 0.2px;
}
.week-lbl.today { color: var(--t1); font-weight: 600; }

/* ── Project Breakdown card ──────────────────────────────────────────────── */
.proj-card { padding: 12px 14px 14px; }
.proj-hdr  { display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
#projBody {
  overflow: hidden; padding: 10px 0 0;
  transition: max-height 0.28s ease, opacity 0.22s ease, padding 0.28s ease;
}
#projBody.open { opacity: 1; }
#projBody.shut { max-height: 0 !important; opacity: 0; padding-top: 0; }
.proj-tabs {
  display: inline-flex; gap: 2px; width: fit-content;
  background: var(--pill); border-radius: 5px; padding: 2px;
  margin-bottom: 10px;
}
.proj-tab {
  font-family: inherit; font-size: 8px; font-weight: 500;
  color: var(--t3); background: none; border: none;
  border-radius: 4px; padding: 2px 9px; cursor: pointer;
  transition: all 0.15s; -webkit-appearance: none; letter-spacing: 0.2px;
}
.proj-tab.active { background: var(--pill-act-bg); color: var(--t1); }
.proj-rows { display: flex; flex-direction: column; gap: 7px; }
.proj-row  { display: flex; align-items: center; gap: 6px; }
.proj-name {
  font-size: 8.5px; color: var(--t2); width: 82px; flex-shrink: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  letter-spacing: 0.05px;
}
.proj-bar-wrap {
  flex: 1; height: 5px;
  background: var(--track); border-radius: 3px; overflow: hidden;
}
.proj-bar {
  height: 100%; border-radius: 3px; width: 0%;
  transition: width 0.55s cubic-bezier(0.34,1.05,0.64,1);
}
.proj-val {
  font-size: 8px; color: var(--t3); opacity: 0.55; width: 34px; text-align: right;
  flex-shrink: 0; font-variant-numeric: tabular-nums; letter-spacing: 0.1px;
}
.proj-pct {
  font-size: 8.5px; color: var(--t2); width: 28px; text-align: right;
  flex-shrink: 0; font-variant-numeric: tabular-nums; letter-spacing: 0.1px; font-weight: 500;
}
.proj-empty {
  font-size: 9px; color: var(--t3); text-align: center;
  padding: 6px 0 2px; letter-spacing: 0.2px; display: none;
}

/* ── Hourly Heatmap card ─────────────────────────────────────────────────── */
.heatmap-card { padding: 12px 14px 12px; }
.heatmap-hdr  { display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
#heatmapBody {
  overflow: hidden; padding: 10px 0 0;
  transition: max-height 0.28s ease, opacity 0.22s ease, padding 0.28s ease;
}
#heatmapBody.open { opacity: 1; }
#heatmapBody.shut { max-height: 0 !important; opacity: 0; padding-top: 0; }
.heatmap-grid { display: flex; flex-direction: column; gap: 2px; }
.heatmap-row  { display: flex; align-items: center; gap: 3px; }
.heatmap-dow  {
  font-size: 6.5px; color: var(--t3); width: 22px; flex-shrink: 0;
  text-align: right; letter-spacing: 0; font-weight: 500;
}
.heatmap-cells { display: flex; gap: 1px; flex: 1; }
.heatmap-cell  {
  flex: 1; height: 9px; border-radius: 1.5px;
  background: rgba(130,100,245,0.07);
  transition: opacity 0.2s;
}
.heatmap-cell:hover { opacity: 0.75; cursor: default; }
.heatmap-axis {
  position: relative; height: 13px;
  margin-top: 4px; margin-left: 25px; /* align with cells (dow label width + gap) */
}
.heatmap-axis-lbl {
  position: absolute; font-size: 7px; color: var(--t3); letter-spacing: 0.1px;
  transform: translateX(-50%);
}
.heatmap-axis-lbl:first-child { transform: none; }

/* Carbon mode — heatmap cells use amber tint */
html[data-vivid="on"] .heatmap-cell {
  background: rgba(255,184,0,0.06);
}

/* ── Footer ── */
.footer {
  padding: 6px 14px 16px;
  display: flex; flex-direction: column; gap: 7px;
  margin-top: 6px;
}
.footer-row { display: flex; align-items: center; justify-content: space-between; }
.footer-lbl {
  font-size: 8.5px; font-weight: 600;
  letter-spacing: 1.2px; color: var(--t3);
  text-transform: uppercase;
}


/* ── Expired cookie banner ── */
.expired-banner {
  display: none;
  align-items: center; justify-content: space-between;
  gap: 8px;
  margin: 10px 14px 0;
  padding: 9px 12px;
  background: rgba(230, 140, 20, 0.15);
  border: 1px solid rgba(230, 140, 20, 0.40);
  border-radius: 10px;
  font-size: 11px; color: rgba(255, 185, 80, 0.95);
  letter-spacing: 0.1px; line-height: 1.3;
  flex-shrink: 0;
}
html[data-expired="on"] .expired-banner { display: flex; }
.expired-banner-text { flex: 1; }
.expired-banner-text b { color: rgba(255, 200, 100, 1); font-weight: 600; }
.expired-update-btn {
  flex-shrink: 0;
  background: rgba(230, 140, 20, 0.25);
  border: 1px solid rgba(230, 140, 20, 0.50);
  border-radius: 6px; padding: 4px 9px;
  font-size: 10.5px; font-weight: 600;
  color: rgba(255, 200, 80, 1);
  cursor: pointer; -webkit-appearance: none;
  transition: background 0.15s;
}
.expired-update-btn:hover { background: rgba(230, 140, 20, 0.40); }
/* Stale gauge — desaturate arc + needle when expired */
html[data-expired="on"] .arc-svg {
  filter: saturate(0.12) brightness(0.75);
  transition: filter 0.4s ease;
}
html[data-expired="on"] .arc-svg .gauge-carbon { filter: none; }
/* Last synced label */
#lastSynced {
  display: none;
  font-size: 10px; color: rgba(255,185,80,0.75);
  text-align: center; letter-spacing: 0.2px;
  margin-top: 2px;
}
html[data-expired="on"] #lastSynced { display: block; }

/* ── Expired — amber input border (dark mode) ── */
html[data-expired="on"] .cookie-form.open .cookie-input:not(.cookie-typed) {
  border-color: rgba(230, 140, 20, 0.65);
}

/* ── Expired — light mode contrast overrides ── */
html[data-expired="on"][data-theme="light"] .expired-banner {
  background: rgba(200, 120, 0, 0.10);
  border-color: rgba(180, 100, 0, 0.45);
  color: rgba(130, 70, 0, 0.95);
}
html[data-expired="on"][data-theme="light"] .expired-banner-text b {
  color: rgba(120, 60, 0, 1);
}
html[data-expired="on"][data-theme="light"] .expired-update-btn {
  background: rgba(200, 120, 0, 0.18);
  border-color: rgba(180, 100, 0, 0.45);
  color: rgba(120, 60, 0, 1);
}
html[data-expired="on"][data-theme="light"] .expired-update-btn:hover {
  background: rgba(200, 120, 0, 0.28);
}
html[data-expired="on"][data-theme="light"] #lastSynced {
  color: rgba(140, 80, 0, 0.80);
}
/* Amber input border — light mode */
html[data-expired="on"][data-theme="light"] .cookie-form.open .cookie-input:not(.cookie-typed) {
  border-color: rgba(180, 100, 0, 0.60);
}

/* ── Onboarding overlay ── */
#app { position: relative; }
.ob-overlay {
  display: none;
  position: absolute; inset: 0; z-index: 100;
  background: linear-gradient(175deg, var(--g1) 0%, var(--g2) 50%, var(--g3) 100%);
  border-radius: 14px;
  flex-direction: column; align-items: center; justify-content: center;
  padding: 28px 28px 24px; gap: 0;
}
html[data-onboarding="on"] .ob-overlay { display: flex; }
.ob-gauge {
  margin-bottom: 18px; opacity: 0.92;
}
.ob-title {
  font-size: 17px; font-weight: 300; letter-spacing: -0.3px;
  color: var(--t1); text-align: center; margin-bottom: 5px;
}
.ob-sub {
  font-size: 10.5px; color: var(--t2);
  text-align: center; letter-spacing: 0.1px; line-height: 1.5;
  margin-bottom: 20px; max-width: 200px;
}
.ob-feats {
  display: flex; flex-direction: column; gap: 9px;
  width: 100%; margin-bottom: 24px;
}
.ob-feat {
  display: flex; align-items: center; gap: 9px;
  font-size: 10.5px; color: var(--t2); letter-spacing: 0.1px;
}
.ob-feat-dot {
  width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0;
  background: linear-gradient(135deg, #52BAFF, #A87CF5);
}
html[data-theme="light"] .ob-feat-dot {
  background: linear-gradient(135deg, #2A7FC0, #7B50C8);
}
.ob-cta {
  width: 100%;
  background: rgba(255,255,255,0.10);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 10px; padding: 11px 0;
  font-size: 12px; font-weight: 600; letter-spacing: 0.3px;
  color: var(--t1); cursor: pointer; -webkit-appearance: none;
  transition: background 0.15s, border-color 0.15s;
  margin-bottom: 13px;
}
.ob-cta:hover {
  background: rgba(255,255,255,0.16);
  border-color: rgba(255,255,255,0.28);
}
html[data-theme="light"] .ob-cta {
  background: rgba(0,0,0,0.07);
  border-color: rgba(0,0,0,0.18);
}
html[data-theme="light"] .ob-cta:hover {
  background: rgba(0,0,0,0.12);
}
@keyframes ob-text-shimmer {
  0%   { background-position: 0% center; }
  100% { background-position: 200% center; }
}
.ob-cta-text {
  background: linear-gradient(90deg, #ffffff, #97D6FF, #CBB0F9, #F7A1B3, #ffffff);
  background-size: 250% auto;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  animation: ob-text-shimmer 3s linear infinite;
}
html[data-theme="light"] .ob-cta-text {
  background: linear-gradient(90deg, #1A2A4A, #7FB2D9, #B096DE, #D983A0, #1A2A4A);
  background-size: 250% auto;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  animation: ob-text-shimmer 3s linear infinite;
}
.ob-note {
  font-size: 9px; color: var(--t3); text-align: center;
  letter-spacing: 0.2px;
}
@keyframes ob-arrow-nudge {
  0%, 100% { transform: translateX(0px); }
  50%       { transform: translateX(5px); }
}
.ob-arrow {
  display: inline-block;
  animation: ob-arrow-nudge 1.3s ease-in-out infinite;
}
/* Needle sweeps in from arc-start (135°) to resting (297°) = 162° rotation, with bounce */
@keyframes ob-needle-in {
  0%   { transform: rotate(-162deg); }
  72%  { transform: rotate(9deg);    }
  84%  { transform: rotate(-5deg);   }
  92%  { transform: rotate(3deg);    }
  100% { transform: rotate(0deg);    }
}
.ob-needle-g {
  transform-box: view-box;
  transform-origin: 50% 58.14%;   /* CX/viewW=130/260, CY/viewH=125/215 */
  animation: ob-needle-in 1.1s ease-out 0.85s both;  /* delayed so arc draws first */
}

/* ── Onboarding: arc draw ── */
@keyframes ob-arc-draw {
  from { stroke-dasharray: 0 445; }
  to   { stroke-dasharray: 267 445; }
}
#obArcFill {
  animation: ob-arc-draw 0.85s ease-out 0.10s both;
}

/* ── Onboarding: staggered fade-up entrance ── */
@keyframes ob-fade-up {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0);   }
}
.ob-gauge                             { animation: ob-fade-up 0.5s ease-out 0.05s both; }
.ob-title                             { animation: ob-fade-up 0.5s ease-out 0.25s both; }
.ob-sub                               { animation: ob-fade-up 0.5s ease-out 0.40s both; }
.ob-feats .ob-feat:nth-child(1)       { animation: ob-fade-up 0.5s ease-out 0.55s both; }
.ob-feats .ob-feat:nth-child(2)       { animation: ob-fade-up 0.5s ease-out 0.63s both; }
.ob-feats .ob-feat:nth-child(3)       { animation: ob-fade-up 0.5s ease-out 0.71s both; }
.ob-cta                               { animation: ob-fade-up 0.5s ease-out 0.85s both; }
.ob-note                              { animation: ob-fade-up 0.5s ease-out 0.95s both; }

/* ── Onboarding: bullet dot pop-in with bounce ── */
@keyframes ob-dot-pop {
  0%   { transform: scale(0);   }
  70%  { transform: scale(1.4); }
  100% { transform: scale(1);   }
}
.ob-feats .ob-feat:nth-child(1) .ob-feat-dot { animation: ob-dot-pop 0.4s ease-out 0.65s both; }
.ob-feats .ob-feat:nth-child(2) .ob-feat-dot { animation: ob-dot-pop 0.4s ease-out 0.73s both; }
.ob-feats .ob-feat:nth-child(3) .ob-feat-dot { animation: ob-dot-pop 0.4s ease-out 0.81s both; }

/* ── Onboarding: slow aurora background hue drift ── */
@keyframes ob-aurora-shift {
  0%, 100% { filter: hue-rotate(0deg)  brightness(1.00); }
  50%       { filter: hue-rotate(18deg) brightness(1.04); }
}
html[data-onboarding="on"] .ob-overlay {
  animation: ob-aurora-shift 8s ease-in-out infinite;
}

/* ── Empty state ── */
.empty-state {
  display: none; flex-direction: column; align-items: center;
  gap: 3px; margin-top: 4px;
}
html[data-empty="on"] .empty-state { display: flex; }
html[data-empty="on"] .arc-svg { opacity: 0.38; transition: opacity 0.3s ease; }
.empty-msg {
  font-size: 11px; font-weight: 500; color: var(--t2);
  letter-spacing: 0.1px; text-align: center;
}
.empty-sub {
  font-size: 9.5px; color: var(--t3);
  letter-spacing: 0.1px; text-align: center; line-height: 1.4;
  max-width: 190px;
}

/* ── Carbon mode overrides ── */
html[data-vivid="on"] {
  /* Background */
  --g1: #0C0E11;
  --g2: #0E1014;
  --g3: #111318;
  /* Cards: explicit color as requested */
  --card:   #191B1F;
  --card-b: rgba(255,255,255,0.09);
  --div:    rgba(255,255,255,0.08);
  /* Arc gradient: red → warm amber → cool blue (reference palette) */
  --grad-start: #E04040;
  --grad-mid:   #FFB800;
  --grad-end:   #68C4FF;
  /* Track dots slightly more visible against the darker bg */
  --track: rgba(255,255,255,0.14);
  /* Hero text + today bar: warm amber tint to match arc colors */
  --arc-fill: rgba(255,215,140,0.92);
  --bar-today: rgba(255,195,80,0.85);
  --bar-idle:  rgba(255,255,255,0.10);
}
/* Carbon mode — efficiency segment palette (good→bad: blue→amber→orange-red) */
html[data-vivid="on"] #es0 { background: #AAD7FE !important; }
html[data-vivid="on"] #es1 { background: #98CECC !important; }
html[data-vivid="on"] #es2 { background: #ECB967 !important; }
html[data-vivid="on"] #es3 { background: #F09060 !important; }
html[data-vivid="on"] #es4 { background: #FF654D !important; }

/* ── Carbon + Light mode overrides ── */
html[data-vivid="on"][data-theme="light"] {
  --g1: #E2E5E9;
  --g2: #E9ECEF;
  --g3: #F0F2F5;
  --card:   rgba(255,255,255,0.82);
  --card-b: rgba(255,255,255,0.96);
  --t1: rgba(10,14,22,0.95);
  --t2: rgba(10,14,22,0.60);
  --t3: rgba(10,14,22,0.42);
  --div:  rgba(0,0,0,0.09);
  --track: rgba(0,0,0,0.18);
  --arc-fill: rgba(15,35,75,0.88);
  --dot-fill: #1C2840;
  --bar-today: rgba(185,115,15,0.88);
  --bar-idle:  rgba(0,0,0,0.11);
  --grad-start: #C03030;
  --grad-mid:   #C88800;
  --grad-end:   #3888CC;
  --tick: rgba(10,14,22,1);
  --pill: rgba(0,0,0,0.08);
  --pill-act-bg: rgba(0,0,0,0.16);
  --pill-act-border: rgba(0,0,0,0.30);
  --pill-act-color: rgba(10,14,22,1.0);
  --theme-active: rgba(0,0,0,0.15);
}
/* Carbon light — dot ring visible on light bg */
html[data-vivid="on"][data-theme="light"] .carbon-ring-seg {
  stroke: rgba(0,0,0,0.22) !important;
}
/* Carbon light — tooltip & panel backgrounds */
html[data-vivid="on"][data-theme="light"] .eff-tooltip {
  background: rgba(255,255,255,0.99);
  border-color: rgba(0,0,0,0.12);
}
html[data-vivid="on"][data-theme="light"] .eff-tooltip::after {
  border-top-color: rgba(255,255,255,0.99);
}
html[data-vivid="on"][data-theme="light"] .cookie-warn-tip {
  background: rgba(255,255,255,0.99);
  border-color: rgba(220,140,30,0.55);
}
html[data-vivid="on"][data-theme="light"] .cookie-warn-tip::before {
  border-top-color: rgba(255,255,255,0.99);
}
html[data-vivid="on"][data-theme="light"] .info-panel {
  background: rgba(255,255,255,0.99);
  border-color: rgba(0,0,0,0.12);
}
/* Carbon light — info panel text */
html[data-vivid="on"][data-theme="light"] .info-title               { color: rgba(10,14,22,0.90); }
html[data-vivid="on"][data-theme="light"] .info-steps li            { color: rgba(10,14,22,0.72); }
html[data-vivid="on"][data-theme="light"] .info-steps li::before    { color: rgba(10,14,22,0.65); background: rgba(0,0,0,0.10); }
html[data-vivid="on"][data-theme="light"] .info-steps b             { color: rgba(10,14,22,0.95); }
html[data-vivid="on"][data-theme="light"] .info-steps kbd           { color: rgba(10,14,22,0.80); background: rgba(0,0,0,0.07); border-color: rgba(0,0,0,0.18); }

/* Carbon light — efficiency tooltip text */
html[data-vivid="on"][data-theme="light"] .eff-tip-title            { color: rgba(10,14,22,0.90); }
html[data-vivid="on"][data-theme="light"] .eff-tip-body             { color: rgba(10,14,22,0.65); }
html[data-vivid="on"][data-theme="light"] .eff-tip-body b           { color: rgba(10,14,22,0.85); }

/* Carbon light — cookie warn popup text */
html[data-vivid="on"][data-theme="light"] .cookie-warn-body         { color: rgba(10,14,22,0.65); }

/* ── Aurora light mode — tooltip / panel backgrounds ── */
html[data-theme="light"]:not([data-vivid="on"]) .eff-tooltip {
  background: rgba(255,255,255,0.99);
  border-color: rgba(0,0,0,0.10);
  box-shadow: 0 6px 24px rgba(0,0,0,0.14);
}
html[data-theme="light"]:not([data-vivid="on"]) .eff-tooltip::before {
  border-top-color: rgba(0,0,0,0.10);
}
html[data-theme="light"]:not([data-vivid="on"]) .eff-tooltip::after {
  border-top-color: rgba(255,255,255,0.99);
}
html[data-theme="light"]:not([data-vivid="on"]) .eff-tip-title  { color: rgba(10,14,22,0.90); }
html[data-theme="light"]:not([data-vivid="on"]) .eff-tip-body   { color: rgba(10,14,22,0.62); }
html[data-theme="light"]:not([data-vivid="on"]) .eff-tip-body b { color: rgba(10,14,22,0.85); }

html[data-theme="light"]:not([data-vivid="on"]) .cookie-warn-tip {
  background: rgba(255,255,255,0.99);
  border-color: rgba(200,130,20,0.45);
  box-shadow: 0 6px 24px rgba(0,0,0,0.12);
}
html[data-theme="light"]:not([data-vivid="on"]) .cookie-warn-tip::after  { border-top-color: rgba(200,130,20,0.45); }
html[data-theme="light"]:not([data-vivid="on"]) .cookie-warn-tip::before { border-top-color: rgba(255,255,255,0.99); }
html[data-theme="light"]:not([data-vivid="on"]) .cookie-warn-body        { color: rgba(10,14,22,0.65); }

html[data-theme="light"]:not([data-vivid="on"]) .info-panel {
  background: rgba(255,255,255,0.99);
  border-color: rgba(0,0,0,0.10);
  box-shadow: 0 6px 24px rgba(0,0,0,0.14);
}
html[data-theme="light"]:not([data-vivid="on"]) .info-title        { color: rgba(10,14,22,0.90); }
html[data-theme="light"]:not([data-vivid="on"]) .info-steps li     { color: rgba(10,14,22,0.72); }
html[data-theme="light"]:not([data-vivid="on"]) .info-steps li::before { color: rgba(10,14,22,0.65); background: rgba(0,0,0,0.10); }
html[data-theme="light"]:not([data-vivid="on"]) .info-steps b      { color: rgba(10,14,22,0.95); }
html[data-theme="light"]:not([data-vivid="on"]) .info-steps kbd    { color: rgba(10,14,22,0.80); background: rgba(0,0,0,0.07); border-color: rgba(0,0,0,0.18); }

/* ── Carbon light — softer tooltip shadows ── */
html[data-vivid="on"][data-theme="light"] .eff-tooltip    { box-shadow: 0 4px 16px rgba(0,0,0,0.10); }
html[data-vivid="on"][data-theme="light"] .cookie-warn-tip { box-shadow: 0 4px 16px rgba(0,0,0,0.09); }
html[data-vivid="on"][data-theme="light"] .info-panel      { box-shadow: 0 4px 16px rgba(0,0,0,0.10); }

/* Carbon light — session label contrast */
html[data-vivid="on"][data-theme="light"] #gaugeSessionLbl { opacity: 0.55; }
/* Carbon light — LOW arc + label: darken blue for contrast on light bg */
html[data-vivid="on"][data-theme="light"] #arcLow { stroke: #3A90C8 !important; }
html[data-vivid="on"][data-theme="light"] #arcLblLow { fill: #3A90C8 !important; }
html[data-vivid="on"][data-theme="light"] #es0 { background: #3A90C8 !important; }
html[data-vivid="on"][data-theme="light"] .eff-tooltip .tip-row:nth-child(1) .tip-dot { background: #3A90C8 !important; }

/* Vivid dot button */
.vivid-btn {
  width: 16px; height: 16px; border-radius: 50%;
  background: conic-gradient(from 135deg,
    #E84040 0%, #FF8C00 28%, #FFB800 52%, #50B8FF 78%, #E84040 100%);
  border: 1.5px solid transparent;
  cursor: pointer; -webkit-appearance: none; flex-shrink: 0;
  transition: opacity 0.2s, border-color 0.2s, box-shadow 0.2s;
  opacity: 0.28;
}
.vivid-btn.active {
  opacity: 1;
  border-color: rgba(255,200,100,0.55);
  box-shadow: 0 0 7px rgba(255,160,50,0.40);
}

/* Theme toggle — single button */
.theme-btn {
  font-family: inherit; font-size: 9px; font-weight: 500;
  color: var(--t2); background: var(--pill);
  border: 1px solid transparent; border-radius: 6px;
  padding: 3px 10px; cursor: pointer;
  transition: all 0.15s; -webkit-appearance: none;
  display: inline-flex; align-items: center; gap: 5px;
  letter-spacing: 0.2px; line-height: 1;
}
.theme-btn:hover {
  background: var(--pill-act-bg);
  border-color: var(--pill-act-border);
  color: var(--pill-act-color);
}

.footer-updated {
  font-size: 8.5px; color: var(--t3);
  letter-spacing: 0.2px;
}
.refresh-btn {
  font-family: inherit; font-size: 11px;
  color: var(--t3); background: none;
  border: none; padding: 0 2px; cursor: pointer;
  line-height: 1; transition: color 0.15s, transform 0.3s;
  -webkit-appearance: none;
}
.refresh-btn:hover { color: var(--t2); }
.refresh-btn.spinning { animation: spin 0.7s linear; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* Cookie key row */
.cookie-btn {
  font-family: inherit; font-size: 9px; font-weight: 500;
  color: var(--t2); background: var(--pill);
  border: 1px solid transparent; border-radius: 6px;
  padding: 3px 10px; cursor: pointer;
  transition: all 0.15s; -webkit-appearance: none;
  letter-spacing: 0.3px;
}
.cookie-btn:hover {
  background: var(--pill-act-bg);
  border-color: var(--pill-act-border);
  color: var(--pill-act-color);
}
.cookie-btn.active {
  background: var(--pill-act-bg);
  border-color: var(--pill-act-border);
  color: var(--pill-act-color);
}
.cookie-form {
  overflow: hidden;
  max-height: 0;
  opacity: 0;
  transition: max-height 0.25s ease, opacity 0.2s ease;
  display: flex; flex-direction: column; gap: 5px;
}
.cookie-form.open { max-height: 120px; opacity: 1; }
.cookie-input {
  width: 100%;
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 8px;
  color: var(--t1);
  font-size: 9.5px;
  padding: 7px 10px;
  resize: none;
  outline: none;
  font-family: 'SF Mono', ui-monospace, Menlo, monospace;
  line-height: 1.4;
}
.cookie-input::placeholder { color: var(--t3); }
.cookie-input:focus { border-color: rgba(255,255,255,0.3); }
/* Onboarding highlight — soft pulsing blue border */
@keyframes ob-pulse {
  0%, 100% { border-color: rgba(82, 186, 255, 0.75); box-shadow: 0 0 0 2px rgba(82,186,255,0.12); }
  50%       { border-color: rgba(168,124,245, 0.85); box-shadow: 0 0 0 3px rgba(168,124,245,0.15); }
}
.cookie-input.ob-highlight {
  animation: ob-pulse 2s ease-in-out infinite;
}
.cookie-input.ob-highlight:focus,
.cookie-input.ob-highlight.cookie-typed {
  animation: none;
  border-color: rgba(255,255,255,0.3);
  box-shadow: none;
}
html[data-theme="light"] .cookie-input.ob-highlight {
  animation: none;
  border-color: rgba(82, 186, 255, 0.80);
  box-shadow: 0 0 0 3px rgba(82,186,255,0.15);
}
html[data-theme="light"] .cookie-input.ob-highlight:focus,
html[data-theme="light"] .cookie-input.ob-highlight.cookie-typed {
  border-color: rgba(0,0,0,0.42);
  box-shadow: none;
}
.cookie-actions { display: flex; gap: 5px; }
.cookie-save {
  font-family: inherit; font-size: 9px; font-weight: 600;
  background: rgba(255,255,255,0.18); color: var(--t1);
  border: 1px solid rgba(255,255,255,0.30);
  border-radius: 6px; padding: 4px 14px; cursor: pointer;
  transition: all 0.15s; -webkit-appearance: none;
}
.cookie-save:hover { background: rgba(255,255,255,0.26); }
.cookie-cancel {
  font-family: inherit; font-size: 9px; font-weight: 500;
  background: transparent; color: var(--t3);
  border: 1px solid transparent;
  border-radius: 6px; padding: 4px 10px; cursor: pointer;
  transition: all 0.15s; -webkit-appearance: none;
}
.cookie-cancel:hover { color: var(--t2); }

/* Light mode overrides for cookie form borders */
html[data-theme="light"] .cookie-input {
  background: rgba(0,0,0,0.04);
  border-color: rgba(0,0,0,0.22);
}
html[data-theme="light"] .cookie-input:focus {
  border-color: rgba(0,0,0,0.42);
}
html[data-theme="light"] .cookie-save {
  background: rgba(0,0,0,0.08);
  border-color: rgba(0,0,0,0.28);
}
html[data-theme="light"] .cookie-save:hover {
  background: rgba(0,0,0,0.14);
}
html[data-theme="light"] .cookie-cancel {
  border-color: rgba(0,0,0,0.15);
}
html[data-theme="light"] .cookie-cancel:hover {
  border-color: rgba(0,0,0,0.25);
}

.cookie-status {
  font-size: 8.5px; color: var(--t3);
  text-align: right; letter-spacing: 0.2px;
  min-height: 11px;
}
.cookie-status.ok  { color: rgba(120,220,120,0.8); }
.cookie-status.err { color: rgba(220,100,100,0.8); }

/* Cookie expired warning badge */
.cookie-warn-wrap { position: relative; display: inline-flex; align-items: center; }
.cookie-warn {
  font-size: 10px; cursor: pointer;
  color: rgba(255,160,50,0.9);
  transition: color 0.15s;
  display: none;
  line-height: 1;
}
.cookie-warn:hover { color: rgba(255,185,80,1); }
.cookie-warn-tip {
  position: absolute;
  bottom: calc(100% + 7px);
  left: 50%; transform: translateX(-50%);
  background: rgba(30,16,4,0.97);
  border: 1px solid rgba(255,160,50,0.4);
  border-radius: 9px;
  padding: 10px 12px;
  width: 188px;
  pointer-events: none; opacity: 0;
  transition: opacity 0.18s ease, transform 0.18s ease;
  transform: translateX(-50%) translateY(4px);
  z-index: 20;
}
.cookie-warn-tip.open {
  pointer-events: auto; opacity: 1;
  transform: translateX(-50%) translateY(0);
}
/* arrow */
.cookie-warn-tip::after {
  content: '';
  position: absolute; top: 100%; left: 50%;
  transform: translateX(-50%);
  border: 5px solid transparent;
  border-top-color: rgba(255,160,50,0.4);
}
.cookie-warn-tip::before {
  content: '';
  position: absolute; top: calc(100% + 1px); left: 50%;
  transform: translateX(-50%);
  border: 5px solid transparent;
  border-top-color: rgba(30,16,4,0.97);
  z-index: 1;
}
.cookie-warn-title {
  font-size: 10px; font-weight: 600;
  color: rgba(255,160,50,1);
  letter-spacing: 0.3px; margin-bottom: 5px;
}
.cookie-warn-body {
  font-size: 9.5px; color: rgba(255,255,255,0.65);
  line-height: 1.5; text-align: left;
}

/* Info icon */
.info-icon-btn {
  font-family: inherit; font-size: 11px; line-height: 1;
  color: var(--t2); background: none; border: none;
  padding: 0 2px; cursor: pointer; transition: color 0.15s;
  -webkit-appearance: none; flex-shrink: 0;
}
.info-icon-btn:hover { color: var(--t1); }

/* Info panel overlay */
.info-panel {
  position: absolute;
  left: 14px; right: 14px;
  bottom: 88px;            /* sits just above the theme/refresh row */
  background: rgba(16,32,62,0.97);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 12px;
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  text-align: left; word-spacing: normal;
  padding: 14px 15px 13px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  opacity: 0; pointer-events: none;
  transform: translateY(6px);
  transition: opacity 0.2s ease, transform 0.2s ease;
  z-index: 10;
}
.info-panel.open { opacity: 1; pointer-events: auto; transform: translateY(0); }
.info-hdr {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 10px;
}
.info-title {
  font-size: 10px; font-weight: 600;
  letter-spacing: 0.8px; color: rgba(255,255,255,0.9);
  text-transform: uppercase;
}
.info-close {
  font-size: 15px; color: var(--t3); background: none;
  border: none; cursor: pointer; padding: 4px 6px; line-height: 1;
  -webkit-appearance: none; transition: color 0.15s;
}
.info-close:hover { color: var(--t1); }
.info-steps {
  list-style: none; padding: 0; margin: 0;
  display: flex; flex-direction: column; gap: 6px;
  counter-reset: step;
}
.info-steps li {
  display: flex; gap: 8px; align-items: flex-start;
  font-size: 10px; color: rgba(255,255,255,0.7); line-height: 1.4;
  counter-increment: step;
  text-align: left !important; word-spacing: normal; word-break: normal;
}
.info-steps li > span {
  text-align: left !important; display: block;
}
.info-steps li::before {
  content: counter(step);
  min-width: 16px; height: 16px;
  background: rgba(255,255,255,0.12);
  border-radius: 50%;
  font-size: 8.5px; font-weight: 600;
  color: rgba(255,255,255,0.6);
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; margin-top: 1px;
}
.info-steps b { color: rgba(255,255,255,0.95); font-weight: 600; }
.info-steps kbd {
  font-family: 'SF Mono', ui-monospace, monospace;
  font-size: 9px;
  background: rgba(255,255,255,0.12);
  border: 1px solid rgba(255,255,255,0.2);
  border-radius: 4px; padding: 1px 4px;
  color: rgba(255,255,255,0.85);
}
</style>
</head>
<body>
<div id="app">

  <!-- ── Onboarding overlay — visible only on first launch ── -->
  <div class="ob-overlay" id="obOverlay">
    <!-- Mini gauge icon -->
    <div class="ob-gauge">
      <svg width="72" height="58" viewBox="0 0 260 215">
        <defs>
          <linearGradient id="obGrad" gradientUnits="userSpaceOnUse" x1="65" y1="0" x2="195" y2="0">
            <stop offset="0%"   stop-color="#52BAFF"/>
            <stop offset="50%"  stop-color="#A87CF5"/>
            <stop offset="100%" stop-color="#F26280"/>
          </linearGradient>
        </defs>
        <!-- Track -->
        <path d="M 64.9 190.1 A 92 92 0 1 1 195.1 190.1"
          fill="none" stroke="rgba(255,255,255,0.10)" stroke-width="8" stroke-linecap="round"/>
        <!-- Filled arc ~60% — animated draw via ob-arc-draw -->
        <path id="obArcFill" d="M 64.9 190.1 A 92 92 0 1 1 195.1 190.1"
          fill="none" stroke="url(#obGrad)" stroke-width="8" stroke-linecap="round"
          stroke-dasharray="267 445"/>
        <!-- Needle — triangular polygon at resting 60% position, animated in from 0% -->
        <g class="ob-needle-g">
          <polygon points="171.8,43.0 133.1,126.6 126.9,123.4"
            fill="rgba(255,255,255,0.88)"/>
        </g>
        <!-- Hub outer glow -->
        <circle cx="130" cy="125" r="10" fill="rgba(255,255,255,0.12)"/>
        <!-- Hub -->
        <circle cx="130" cy="125" r="4.5" fill="rgba(255,255,255,0.70)"/>
      </svg>
    </div>
    <div class="ob-title">Claude Token Meter</div>
    <div class="ob-sub">Track your Claude Code token usage in real time</div>
    <div class="ob-feats">
      <div class="ob-feat"><span class="ob-feat-dot"></span>Monthly, weekly &amp; session usage</div>
      <div class="ob-feat"><span class="ob-feat-dot"></span>7-day usage history chart</div>
      <div class="ob-feat"><span class="ob-feat-dot"></span>Connect claude.ai for live data</div>
    </div>
    <button class="ob-cta" id="obCta"><span class="ob-cta-text">Get Started</span> <span class="ob-arrow">→</span></button>
    <div class="ob-note">Reads from ~/.claude · No data leaves your Mac</div>
  </div>

  <!-- Expired cookie banner — visible only when data-expired="on" -->
  <div class="expired-banner" id="expiredBanner">
    <span class="expired-banner-text"><b>Session expired</b> — data shown may be outdated</span>
    <button class="expired-update-btn" id="expiredUpdateBtn">Reconnect →</button>
  </div>

  <div class="hdr">
    <span class="hdr-title">Claude Usage</span>
    <div style="display:flex;align-items:center;gap:4px;">
      <span class="footer-updated" id="updated">—</span>
      <button class="refresh-btn" id="refreshNow" title="Refresh now">↻</button>
    </div>
  </div>

  <div class="arc-wrap">
    <svg class="arc-svg" width="260" height="215" viewBox="0 0 260 215">
      <defs>
        <linearGradient id="gaugeGrad" gradientUnits="userSpaceOnUse" x1="65" y1="0" x2="195" y2="0">
          <stop offset="0%"   stop-color="var(--grad-start)"/>
          <stop offset="50%"  stop-color="var(--grad-mid)"/>
          <stop offset="100%" stop-color="var(--grad-end)"/>
        </linearGradient>
      </defs>

      <!-- ── Default gauge (hidden in Carbon mode) ─────────────────────── -->
      <g class="gauge-default">
        <path d="M 64.9 190.1 A 92 92 0 1 1 195.1 190.1"
          fill="none" stroke="var(--track)" stroke-width="4" stroke-linecap="round"/>
        <path id="gaugeFill"
          d="M 64.9 190.1 A 92 92 0 1 1 195.1 190.1"
          fill="none" stroke="url(#gaugeGrad)" stroke-width="4" stroke-linecap="round"
          stroke-dasharray="0 445"
          style="transition: stroke-dasharray 0.8s cubic-bezier(0.34,1.05,0.64,1);"/>
        <!-- Major ticks: 0%, 25%, 50%, 75%, 100% — same length as minor (r=82→85) -->
        <g stroke="var(--tick)" stroke-linecap="round" stroke-width="1.5" opacity="0.70">
          <line x1="70.0"  y1="185.1" x2="72.0"  y2="183.0"/>
          <line x1="51.5"  y1="92.5"  x2="54.2"  y2="93.6"/>
          <line x1="130.0" y1="40.0"  x2="130.0" y2="43.0"/>
          <line x1="208.5" y1="92.5"  x2="205.8" y2="93.6"/>
          <line x1="190.1" y1="185.1" x2="188.0" y2="183.0"/>
        </g>
        <!-- Medium ticks -->
        <g stroke="var(--tick)" stroke-linecap="round" stroke-width="0.8" opacity="0.28">
          <line x1="49.2"  y1="151.3" x2="52.0"  y2="150.4"/>
          <line x1="46.1"  y1="111.7" x2="49.1"  y2="112.2"/>
          <line x1="61.2"  y1="75.0"  x2="63.6"  y2="76.8"/>
          <line x1="91.4"  y1="49.3"  x2="92.8"  y2="52.0"/>
          <line x1="168.6" y1="49.3"  x2="167.2" y2="52.0"/>
          <line x1="198.8" y1="75.0"  x2="196.4" y2="76.8"/>
          <line x1="213.9" y1="111.7" x2="210.9" y2="112.2"/>
          <line x1="210.8" y1="151.3" x2="207.9" y2="150.4"/>
        </g>
        <!-- Minor ticks -->
        <g stroke="var(--tick)" stroke-linecap="round" stroke-width="0.8" opacity="0.28">
          <line x1="57.5"  y1="169.4" x2="60.1"  y2="167.8"/>
          <line x1="45.3"  y1="131.7" x2="48.3"  y2="131.4"/>
          <line x1="74.8"  y1="60.4"  x2="76.7"  y2="62.6"/>
          <line x1="110.2" y1="42.3"  x2="110.9" y2="45.3"/>
          <line x1="149.8" y1="42.3"  x2="149.1" y2="45.3"/>
          <line x1="185.2" y1="60.4"  x2="183.3" y2="62.6"/>
          <line x1="214.7" y1="131.7" x2="211.7" y2="131.4"/>
          <line x1="202.5" y1="169.4" x2="199.9" y2="167.8"/>
        </g>
        <!-- Micro ticks every 2.5% (between existing ticks) -->
        <g stroke="var(--tick)" stroke-linecap="round" stroke-width="0.6" opacity="0.18">
          <line x1="63.3" y1="177.7" x2="64.5" y2="176.8"/>
          <line x1="52.9" y1="160.7" x2="54.2" y2="160.1"/>
          <line x1="46.6" y1="141.6" x2="48.1" y2="141.3"/>
          <line x1="45.1" y1="121.7" x2="46.6" y2="121.7"/>
          <line x1="48.2" y1="101.9" x2="49.7" y2="102.3"/>
          <line x1="55.8" y1="83.6"  x2="57.1" y2="84.3"/>
          <line x1="67.5" y1="67.4"  x2="68.6" y2="68.4"/>
          <line x1="82.8" y1="54.3"  x2="83.7" y2="55.5"/>
          <line x1="100.5" y1="45.3" x2="101.0" y2="46.7"/>
          <line x1="120.0" y1="40.6" x2="120.2" y2="42.1"/>
          <line x1="140.0" y1="40.6" x2="139.8" y2="42.1"/>
          <line x1="159.5" y1="45.3" x2="159.0" y2="46.7"/>
          <line x1="177.2" y1="54.3" x2="176.3" y2="55.5"/>
          <line x1="192.5" y1="67.4" x2="191.4" y2="68.4"/>
          <line x1="204.2" y1="83.6" x2="202.9" y2="84.3"/>
          <line x1="211.8" y1="101.9" x2="210.3" y2="102.3"/>
          <line x1="214.9" y1="121.7" x2="213.4" y2="121.7"/>
          <line x1="213.4" y1="141.6" x2="211.9" y2="141.3"/>
          <line x1="207.1" y1="160.7" x2="205.8" y2="160.1"/>
          <line x1="196.7" y1="177.7" x2="195.5" y2="176.8"/>
        </g>
        <!-- Numeric labels -->
        <text x="59"  y="202" fill="var(--t3)" font-family="-apple-system,sans-serif" font-size="9" font-weight="500" text-anchor="middle">0</text>
        <text x="35"  y="86"  fill="var(--t3)" font-family="-apple-system,sans-serif" font-size="9" font-weight="500" text-anchor="end">25</text>
        <text x="130" y="23"  fill="var(--t3)" font-family="-apple-system,sans-serif" font-size="9" font-weight="500" text-anchor="middle">50</text>
        <text x="225" y="86"  fill="var(--t3)" font-family="-apple-system,sans-serif" font-size="9" font-weight="500" text-anchor="start">75</text>
        <text x="201" y="202" fill="var(--t3)" font-family="-apple-system,sans-serif" font-size="9" font-weight="500" text-anchor="middle">100</text>
      </g>

      <!-- ── Carbon gauge (visible only in Carbon mode) ──────────────── -->
      <!--
        Two concentric rings:
          Outer index ring:  r=100, fine dots, gaps at LOW/MEDIUM/HIGH label positions
          Inner color ring:  r=92,  3 permanently-filled segments with deliberate gaps
        Zone boundaries (inner ring, r=92):
          LOW  0–50%:  SVG 135°→266°  (gap ±4° at 50%/SVG270°)
          MED 50–75%:  SVG 274°→333.5° (gap ±4° at 75%/SVG337.5°)
          HIGH 75–100%: SVG 341.5°→45°
        Outer ring gaps (r=100):
          LOW  label at SVG 135° → ring starts at SVG 142°
          MED  label at SVG 270° → ring ends at SVG 260°, restarts at SVG 280°
          HIGH label at SVG  45° → ring ends at SVG  38°
      -->
      <g class="gauge-carbon">

        <!-- Outer index ring r=102 (~7px gap from inner arc edge) -->
        <!-- Segment 1: SVG 142° → 260° (gap for LOW at start, gap for MEDIUM before) -->
        <path class="carbon-ring-seg" d="M 49.6 187.8 A 102 102 0 0 1 112.3 24.5"
          fill="none" stroke="rgba(255,255,255,0.20)" stroke-width="1.2"
          stroke-linecap="round" stroke-dasharray="0.8 5.5"/>
        <!-- Segment 2: SVG 280° → 38° (gap for MEDIUM after, gap for HIGH at end) -->
        <path class="carbon-ring-seg" d="M 147.7 24.5 A 102 102 0 0 1 210.4 187.8"
          fill="none" stroke="rgba(255,255,255,0.20)" stroke-width="1.2"
          stroke-linecap="round" stroke-dasharray="0.8 5.5"/>

        <!-- Inner color arcs — permanently filled, no JS dasharray animation -->
        <!-- LOW  0–50%:  SVG 135°→266°, 131° span -->
        <path id="arcLow"
          d="M 64.9 190.1 A 92 92 0 0 1 123.6 33.2"
          fill="none" stroke="#AAD7FE" stroke-width="4" stroke-linecap="round"/>
        <!-- MED  50–75%: SVG 274°→333.5°, 59.5° span -->
        <path id="arcMed"
          d="M 136.4 33.2 A 92 92 0 0 1 212.3 83.9"
          fill="none" stroke="#ECB967" stroke-width="4" stroke-linecap="round"/>
        <!-- HIGH 75–100%: SVG 341.5°→45°, 63.5° span -->
        <path id="arcHigh"
          d="M 217.2 95.8 A 92 92 0 0 1 195.1 190.1"
          fill="none" stroke="#FF654D" stroke-width="4" stroke-linecap="round"/>

        <!-- Zone labels in the outer ring gap, rotated along the arc -->
        <!-- LOW on outer ring (r=102) at SVG 135°, first letter at x≈58 -->
        <text id="arcLblLow" transform="rotate(-130, 58, 197)" x="58" y="197"
          fill="#AAD7FE" opacity="0.90"
          font-family="-apple-system,sans-serif" font-size="6.5" font-weight="700"
          text-anchor="start" letter-spacing="1.0">LOW</text>
        <!-- MEDIUM at SVG 270° (top), r=102, horizontal -->
        <text x="130" y="23"
          fill="#ECB967" opacity="0.90"
          font-family="-apple-system,sans-serif" font-size="6.5" font-weight="700"
          text-anchor="middle" letter-spacing="1.0">MEDIUM</text>
        <!-- HIGH on outer ring (r=102) at SVG 45°, last letter at x≈202 -->
        <text transform="rotate(130, 202, 197)" x="202" y="197"
          fill="#FF654D" opacity="0.90"
          font-family="-apple-system,sans-serif" font-size="6.5" font-weight="700"
          text-anchor="end" letter-spacing="1.0">HIGH</text>

      </g>

      <!-- ── Always visible: needle · hub · labels ──────────────────── -->
      <polygon id="gaugeNeedle" points="64.9,190.1 127.5,122.5 132.5,127.5"
        fill="var(--dot-fill)" opacity="0.88"/>
      <circle cx="130" cy="125" r="7.5" fill="var(--dot-fill)" opacity="0.12"/>
      <circle cx="130" cy="125" r="4.5" fill="var(--dot-fill)" opacity="0.70"/>
      <circle cx="130" cy="125" r="2"   fill="rgba(0,0,0,0.35)"/>

      <text id="gaugeSessionLbl" x="130" y="95" fill="var(--t3)"
        font-family="-apple-system,sans-serif" font-size="7" font-weight="500"
        text-anchor="middle" letter-spacing="1.5" opacity="0.6">THIS SESSION</text>

      <!-- Number centered at x=130 independently of % sign -->
      <text id="heroPct" x="130" y="188"
        fill="var(--t1)"
        font-family="-apple-system,'SF Pro Display','Helvetica Neue',sans-serif"
        font-weight="200" text-anchor="middle" letter-spacing="-1"
        ><tspan id="heroPctNum" font-size="46">--</tspan></text>
      <!-- % sign repositioned by JS after each number update -->
      <text id="heroPctSign" x="156" y="172"
        fill="var(--t1)"
        font-family="-apple-system,'SF Pro Display','Helvetica Neue',sans-serif"
        font-weight="200" font-size="16" text-anchor="start">%</text>

      <text id="heroStatus" x="130" y="207"
        fill="var(--t2)"
        font-family="-apple-system,'SF Pro Text','Helvetica Neue',sans-serif"
        font-size="10.5" font-weight="400" text-anchor="middle">—</text>
    </svg>
    <div id="lastSynced">⏱ Last synced <span id="lastSyncedTime">—</span></div>
    <div class="empty-state" id="emptyState">
      <div class="empty-msg" id="emptyMsg">No usage this month</div>
      <div class="empty-sub" id="emptySub">Activity appears after using Claude Code</div>
    </div>
  </div>

  <!-- Stats card -->
  <div class="card stats">
    <div class="shimmer-overlay" id="statsShimmer"></div>
    <div class="stat">
      <div class="stat-lbl">THIS SESSION</div>
      <div class="stat-val" id="sessPct"><span id="sessPctNum">--</span><span class="pct-sign">%</span></div>
      <div class="stat-cd" id="sessCd">--</div>
      <div class="stat-reset" id="sessResetTime">resets --</div>
    </div>
    <div class="stat-div"></div>
    <div class="stat">
      <div class="stat-lbl">THIS WEEK</div>
      <div class="stat-val" id="weekPct"><span id="weekPctNum">--</span><span class="pct-sign">%</span></div>
      <div class="stat-cd" id="weekCd">--</div>
      <div class="stat-reset" id="weekResetTime">resets --</div>
    </div>
  </div>

  <!-- Footer -->
  <!-- Efficiency card -->
  <div class="card eff-card" id="effCard">
    <div class="shimmer-overlay" id="effShimmer"></div>
    <div class="eff-hdr" id="effHdr">
      <div class="eff-hdr-left">
        <div class="stat-lbl">PROMPT EFFICIENCY</div>
        <button class="eff-info-btn" id="effInfoBtn" aria-label="How is this calculated?">
          <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
            <circle cx="5.5" cy="5.5" r="5" stroke="currentColor" stroke-width="1.1"/>
            <path d="M5.5 4.8v3" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>
            <circle cx="5.5" cy="3.2" r="0.6" fill="currentColor"/>
          </svg>
        </button>
      </div>
      <button class="eff-toggle" id="effToggle" title="Show / hide">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
    </div>
    <!-- Segment bar — always visible even when collapsed -->
    <div class="eff-segs" style="margin-top:10px">
      <div class="eff-seg" id="es0" style="background:#52BAFF"></div>
      <div class="eff-seg" id="es1" style="background:#8882F0"></div>
      <div class="eff-seg" id="es2" style="background:#A87CF5"></div>
      <div class="eff-seg" id="es3" style="background:#D46090"></div>
      <div class="eff-seg" id="es4" style="background:#F26280"></div>
    </div>
    <!-- Zone labels — always visible even when collapsed -->
    <div class="eff-lbls">
      <span class="eff-lbl" id="el0">Sharp</span>
      <span class="eff-lbl" id="el1">Focused</span>
      <span class="eff-lbl" id="el2">Moderate</span>
      <span class="eff-lbl" id="el3">Verbose</span>
      <span class="eff-lbl" id="el4">Scattered</span>
    </div>
    <div id="effBody">
      <div class="eff-meta" id="effMeta">collecting data…</div>
      <div class="eff-hist">
        <div class="eff-hist-col">
          <div class="stat-lbl">TODAY AVG</div>
          <div class="eff-hist-val" id="effTodayVal">--</div>
        </div>
        <div class="eff-hist-sep"></div>
        <div class="eff-hist-col">
          <div class="stat-lbl">THIS MONTH</div>
          <div class="eff-hist-val" id="effMonthVal">--</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Efficiency info tooltip — lives outside card so overflow:hidden never clips it -->
  <div class="eff-tooltip" id="effTooltip">
    <div class="eff-tip-title">How it's calculated</div>
    <div class="eff-tip-body">
      Measures <b>output ÷ input</b> token ratio this session — how much Claude generates per token you send.
      <div class="tip-row"><span class="tip-dot" style="background:#52BAFF"></span><span><b>Sharp</b> ≥0.30 — concise, high-leverage prompts</span></div>
      <div class="tip-row"><span class="tip-dot" style="background:#8882F0"></span><span><b>Focused</b> 0.18–0.30 — clear with good return</span></div>
      <div class="tip-row"><span class="tip-dot" style="background:#A87CF5"></span><span><b>Moderate</b> 0.10–0.18 — typical usage</span></div>
      <div class="tip-row"><span class="tip-dot" style="background:#D46090"></span><span><b>Verbose</b> 0.05–0.10 — high input overhead</span></div>
      <div class="tip-row"><span class="tip-dot" style="background:#F26280"></span><span><b>Scattered</b> &lt;0.05 — very padded prompts</span></div>
    </div>
  </div>

  <!-- 7-Day Usage card -->
  <div class="card week-card" id="weekChartCard">
    <div class="shimmer-overlay" id="weekChartShimmer"></div>
    <div class="week-hdr" id="weekHdr">
      <span class="stat-lbl">7 DAY USAGE</span>
      <button class="eff-toggle" id="weekChartToggle" title="Show / hide">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
    </div>
    <div id="weekChartBody">
      <div class="week-chart" id="weekChart"></div>
      <div class="week-labels" id="weekLabels"></div>
    </div>
  </div>

  <!-- Project Breakdown card (hidden until module enabled) -->
  <div class="card proj-card" id="projectCard" style="display:none">
    <div class="shimmer-overlay" id="projShimmer"></div>
    <div class="proj-hdr" id="projHdr">
      <span class="stat-lbl">PROJECT BREAKDOWN</span>
      <button class="eff-toggle" id="projToggle" title="Show / hide">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
    </div>
    <div id="projBody">
      <div class="proj-tabs">
        <button class="proj-tab active" id="projTabSess" data-mode="session">Session</button>
        <button class="proj-tab"        id="projTabWeek" data-mode="week">This Week</button>
      </div>
      <div class="proj-rows" id="projRows"></div>
      <div class="proj-empty" id="projEmpty">No project data yet</div>
    </div>
  </div>

  <!-- Hourly Heatmap card (hidden until module enabled) -->
  <div class="card heatmap-card" id="heatmapCard" style="display:none">
    <div class="shimmer-overlay" id="heatmapShimmer"></div>
    <div class="heatmap-hdr" id="heatmapHdr">
      <span class="stat-lbl">HOURLY HEATMAP</span>
      <button class="eff-toggle" id="heatmapToggle" title="Show / hide">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
    </div>
    <div id="heatmapBody">
      <div class="heatmap-grid" id="heatmapGrid"></div>
      <div class="heatmap-axis" id="heatmapAxis"></div>
    </div>
  </div>

  <!-- Footer -->
  <div class="footer">
    <div class="footer-row">
      <div style="display:flex;align-items:center;gap:5px;">
        <span class="footer-lbl">Connect Claude Account</span>
        <div class="cookie-warn-wrap">
          <span class="cookie-warn" id="cookieWarn">⚠</span>
          <div class="cookie-warn-tip" id="cookieWarnTip">
            <div class="cookie-warn-title">Session cookie expired</div>
            <div class="cookie-warn-body">Your session key is no longer valid. Click <b>Update Cookie Key</b> to paste a fresh one from Chrome.</div>
          </div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:5px;">
        <button class="info-icon-btn" id="infoToggle" title="How to connect via browser cookie">ⓘ</button>
        <button class="cookie-btn" id="cookieToggle">Connect →</button>
      </div>
    </div>
    <div class="cookie-form" id="cookieForm">
      <textarea class="cookie-input" id="cookieInput" rows="2"
        placeholder="Paste sessionKey value… (starts with sk-ant-sid01-)"></textarea>
      <div class="cookie-actions">
        <button class="cookie-save" id="cookieSave">Save &amp; Refresh</button>
        <button class="cookie-cancel" id="cookieCancel">Cancel</button>
        <span class="cookie-status" id="cookieStatus"></span>
      </div>
    </div>

    <div class="footer-row">
      <span class="footer-lbl">Appearance</span>
      <div style="display:flex;align-items:center;gap:7px;">
        <button class="vivid-btn" id="vividToggle" title="Carbon mode"></button>
        <button class="theme-btn" id="themeToggle">🌙 Dark</button>
      </div>
    </div>
  </div>

  <!-- Info panel -->
  <div class="info-panel" id="infoPanel">
    <div class="info-hdr">
      <span class="info-title">Connect via Browser Cookie</span>
      <button class="info-close" id="infoClose">✕</button>
    </div>
    <ol class="info-steps">
      <li><span>Open <b>Chrome</b> and go to <b>claude.ai</b></span></li>
      <li><span>Press <kbd>⌘ Option I</kbd> to open DevTools</span></li>
      <li><span>Click the <b>Application</b> tab at the top</span></li>
      <li><span>In the sidebar expand <b>Cookies</b> → click <b>https://claude.ai</b></span></li>
      <li><span>Find the row named <b>sessionKey</b> in the table</span></li>
      <li><span>Right-click its value → <b>Copy value</b> — it starts with <kbd>sk-ant-sid01-</kbd></span></li>
      <li><span>Paste into the field and hit <b>Save &amp; Refresh</b></span></li>
    </ol>
  </div>

</div>
<script>
/* anime.js v3.2.2 — embedded inline */
!function(n,e){"object"==typeof exports&&"undefined"!=typeof module?module.exports=e():"function"==typeof define&&define.amd?define(e):n.anime=e()}(this,function(){"use strict";var i={update:null,begin:null,loopBegin:null,changeBegin:null,change:null,changeComplete:null,loopComplete:null,complete:null,loop:1,direction:"normal",autoplay:!0,timelineOffset:0},M={duration:1e3,delay:0,endDelay:0,easing:"easeOutElastic(1, .5)",round:0},j=["translateX","translateY","translateZ","rotate","rotateX","rotateY","rotateZ","scale","scaleX","scaleY","scaleZ","skew","skewX","skewY","perspective","matrix","matrix3d"],l={CSS:{},springs:{}};function C(n,e,t){return Math.min(Math.max(n,e),t)}function u(n,e){return-1<n.indexOf(e)}function o(n,e){return n.apply(null,e)}var w={arr:function(n){return Array.isArray(n)},obj:function(n){return u(Object.prototype.toString.call(n),"Object")},pth:function(n){return w.obj(n)&&n.hasOwnProperty("totalLength")},svg:function(n){return n instanceof SVGElement},inp:function(n){return n instanceof HTMLInputElement},dom:function(n){return n.nodeType||w.svg(n)},str:function(n){return"string"==typeof n},fnc:function(n){return"function"==typeof n},und:function(n){return void 0===n},nil:function(n){return w.und(n)||null===n},hex:function(n){return/(^#[0-9A-F]{6}$)|(^#[0-9A-F]{3}$)/i.test(n)},rgb:function(n){return/^rgb/.test(n)},hsl:function(n){return/^hsl/.test(n)},col:function(n){return w.hex(n)||w.rgb(n)||w.hsl(n)},key:function(n){return!i.hasOwnProperty(n)&&!M.hasOwnProperty(n)&&"targets"!==n&&"keyframes"!==n}};function d(n){n=/\(([^)]+)\)/.exec(n);return n?n[1].split(",").map(function(n){return parseFloat(n)}):[]}function c(r,t){var n=d(r),e=C(w.und(n[0])?1:n[0],.1,100),a=C(w.und(n[1])?100:n[1],.1,100),o=C(w.und(n[2])?10:n[2],.1,100),n=C(w.und(n[3])?0:n[3],.1,100),u=Math.sqrt(a/e),i=o/(2*Math.sqrt(a*e)),c=i<1?u*Math.sqrt(1-i*i):0,s=i<1?(i*u-n)/c:-n+u;function f(n){var e=t?t*n/1e3:n,e=i<1?Math.exp(-e*i*u)*(+Math.cos(c*e)+s*Math.sin(c*e)):(1+s*e)*Math.exp(-e*u);return 0===n||1===n?n:1-e}return t?f:function(){var n=l.springs[r];if(n)return n;for(var e=0,t=0;;)if(1===f(e+=1/6)){if(16<=++t)break}else t=0;return n=e*(1/6)*1e3,l.springs[r]=n}}function q(e){return void 0===e&&(e=10),function(n){return Math.ceil(C(n,1e-6,1)*e)*(1/e)}}var H=function(b,e,M,t){if(0<=b&&b<=1&&0<=M&&M<=1){var x=new Float32Array(11);if(b!==e||M!==t)for(var n=0;n<11;++n)x[n]=k(.1*n,b,M);return function(n){return b===e&&M===t||0===n||1===n?n:k(r(n),e,t)}}function r(n){for(var e=0,t=1;10!==t&&x[t]<=n;++t)e+=.1;var r=e+.1*((n-x[--t])/(x[t+1]-x[t])),a=O(r,b,M);if(.001<=a){for(var o=n,u=r,i=b,c=M,s=0;s<4;++s){var f=O(u,i,c);if(0===f)return u;u-=(k(u,i,c)-o)/f}return u}if(0===a)return r;for(var l,d,p=n,h=e,g=e+.1,m=b,v=M,y=0;0<(l=k(d=h+(g-h)/2,m,v)-p)?g=d:h=d,1e-7<Math.abs(l)&&++y<10;);return d}};function r(n,e){return 1-3*e+3*n}function k(n,e,t){return((r(e,t)*n+(3*t-6*e))*n+3*e)*n}function O(n,e,t){return 3*r(e,t)*n*n+2*(3*t-6*e)*n+3*e}e={linear:function(){return function(n){return n}}},t={Sine:function(){return function(n){return 1-Math.cos(n*Math.PI/2)}},Expo:function(){return function(n){return n?Math.pow(2,10*n-10):0}},Circ:function(){return function(n){return 1-Math.sqrt(1-n*n)}},Back:function(){return function(n){return n*n*(3*n-2)}},Bounce:function(){return function(n){for(var e,t=4;n<((e=Math.pow(2,--t))-1)/11;);return 1/Math.pow(4,3-t)-7.5625*Math.pow((3*e-2)/22-n,2)}},Elastic:function(n,e){void 0===e&&(e=.5);var t=C(n=void 0===n?1:n,1,10),r=C(e,.1,2);return function(n){return 0===n||1===n?n:-t*Math.pow(2,10*(n-1))*Math.sin((n-1-r/(2*Math.PI)*Math.asin(1/t))*(2*Math.PI)/r)}}},["Quad","Cubic","Quart","Quint"].forEach(function(n,e){t[n]=function(){return function(n){return Math.pow(n,e+2)}}}),Object.keys(t).forEach(function(n){var r=t[n];e["easeIn"+n]=r,e["easeOut"+n]=function(e,t){return function(n){return 1-r(e,t)(1-n)}},e["easeInOut"+n]=function(e,t){return function(n){return n<.5?r(e,t)(2*n)/2:1-r(e,t)(-2*n+2)/2}},e["easeOutIn"+n]=function(e,t){return function(n){return n<.5?(1-r(e,t)(1-2*n))/2:(r(e,t)(2*n-1)+1)/2}}});var e,t,s=e;function P(n,e){if(w.fnc(n))return n;var t=n.split("(")[0],r=s[t],a=d(n);switch(t){case"spring":return c(n,e);case"cubicBezier":return o(H,a);case"steps":return o(q,a);default:return o(r,a)}}function a(n){try{return document.querySelectorAll(n)}catch(n){}}function I(n,e){for(var t,r=n.length,a=2<=arguments.length?e:void 0,o=[],u=0;u<r;u++)u in n&&(t=n[u],e.call(a,t,u,n))&&o.push(t);return o}function f(n){return n.reduce(function(n,e){return n.concat(w.arr(e)?f(e):e)},[])}function p(n){return w.arr(n)?n:(n=w.str(n)?a(n)||n:n)instanceof NodeList||n instanceof HTMLCollection?[].slice.call(n):[n]}function h(n,e){return n.some(function(n){return n===e})}function g(n){var e,t={};for(e in n)t[e]=n[e];return t}function x(n,e){var t,r=g(n);for(t in n)r[t]=(e.hasOwnProperty(t)?e:n)[t];return r}function D(n,e){var t,r=g(n);for(t in e)r[t]=(w.und(n[t])?e:n)[t];return r}function V(n){var e,t,r,a,o,u,i;return w.rgb(n)?(e=/rgb\((\d+,\s*[\d]+,\s*[\d]+)\)/g.exec(t=n))?"rgba("+e[1]+",1)":t:w.hex(n)?(e=(e=n).replace(/^#?([a-f\d])([a-f\d])([a-f\d])$/i,function(n,e,t,r){return e+e+t+t+r+r}),e=/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(e),"rgba("+parseInt(e[1],16)+","+parseInt(e[2],16)+","+parseInt(e[3],16)+",1)"):w.hsl(n)?(t=/hsl\((\d+),\s*([\d.]+)%,\s*([\d.]+)%\)/g.exec(t=n)||/hsla\((\d+),\s*([\d.]+)%,\s*([\d.]+)%,\s*([\d.]+)\)/g.exec(t),n=parseInt(t[1],10)/360,u=parseInt(t[2],10)/100,i=parseInt(t[3],10)/100,t=t[4]||1,0==u?r=a=o=i:(r=c(u=2*i-(i=i<.5?i*(1+u):i+u-i*u),i,n+1/3),a=c(u,i,n),o=c(u,i,n-1/3)),"rgba("+255*r+","+255*a+","+255*o+","+t+")"):void 0;function c(n,e,t){return t<0&&(t+=1),1<t&&--t,t<1/6?n+6*(e-n)*t:t<.5?e:t<2/3?n+(e-n)*(2/3-t)*6:n}}function B(n){n=/[+-]?\d*\.?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(%|px|pt|em|rem|in|cm|mm|ex|ch|pc|vw|vh|vmin|vmax|deg|rad|turn)?$/.exec(n);if(n)return n[1]}function m(n,e){return w.fnc(n)?n(e.target,e.id,e.total):n}function v(n,e){return n.getAttribute(e)}function y(n,e,t){var r,a,o;return h([t,"deg","rad","turn"],B(e))?e:(r=l.CSS[e+t],w.und(r)?(a=document.createElement(n.tagName),(n=n.parentNode&&n.parentNode!==document?n.parentNode:document.body).appendChild(a),a.style.position="absolute",a.style.width=100+t,o=100/a.offsetWidth,n.removeChild(a),n=o*parseFloat(e),l.CSS[e+t]=n):r)}function $(n,e,t){var r;if(e in n.style)return r=e.replace(/([a-z])([A-Z])/g,"$1-$2").toLowerCase(),e=n.style[e]||getComputedStyle(n).getPropertyValue(r)||"0",t?y(n,e,t):e}function b(n,e){return w.dom(n)&&!w.inp(n)&&(!w.nil(v(n,e))||w.svg(n)&&n[e])?"attribute":w.dom(n)&&h(j,e)?"transform":w.dom(n)&&"transform"!==e&&$(n,e)?"css":null!=n[e]?"object":void 0}function W(n){if(w.dom(n)){for(var e,t=n.style.transform||"",r=/(\w+)\(([^)]*)\)/g,a=new Map;e=r.exec(t);)a.set(e[1],e[2]);return a}}function X(n,e,t,r){var a=u(e,"scale")?1:0+(u(a=e,"translate")||"perspective"===a?"px":u(a,"rotate")||u(a,"skew")?"deg":void 0),o=W(n).get(e)||a;return t&&(t.transforms.list.set(e,o),t.transforms.last=e),r?y(n,o,r):o}function T(n,e,t,r){switch(b(n,e)){case"transform":return X(n,e,r,t);case"css":return $(n,e,t);case"attribute":return v(n,e);default:return n[e]||0}}function E(n,e){var t=/^(\*=|\+=|-=)/.exec(n);if(!t)return n;var r=B(n)||0,a=parseFloat(e),o=parseFloat(n.replace(t[0],""));switch(t[0][0]){case"+":return a+o+r;case"-":return a-o+r;case"*":return a*o+r}}function Y(n,e){var t;return w.col(n)?V(n):/\s/g.test(n)?n:(t=(t=B(n))?n.substr(0,n.length-t.length):n,e?t+e:t)}function F(n,e){return Math.sqrt(Math.pow(e.x-n.x,2)+Math.pow(e.y-n.y,2))}function Z(n){for(var e,t=n.points,r=0,a=0;a<t.numberOfItems;a++){var o=t.getItem(a);0<a&&(r+=F(e,o)),e=o}return r}function G(n){if(n.getTotalLength)return n.getTotalLength();switch(n.tagName.toLowerCase()){case"circle":return 2*Math.PI*v(n,"r");case"rect":return 2*v(t=n,"width")+2*v(t,"height");case"line":return F({x:v(t=n,"x1"),y:v(t,"y1")},{x:v(t,"x2"),y:v(t,"y2")});case"polyline":return Z(n);case"polygon":return e=n.points,Z(n)+F(e.getItem(e.numberOfItems-1),e.getItem(0))}var e,t}function Q(n,e){var e=e||{},n=e.el||function(n){for(var e=n.parentNode;w.svg(e)&&w.svg(e.parentNode);)e=e.parentNode;return e}(n),t=n.getBoundingClientRect(),r=v(n,"viewBox"),a=t.width,t=t.height,e=e.viewBox||(r?r.split(" "):[0,0,a,t]);return{el:n,viewBox:e,x:+e[0],y:+e[1],w:a,h:t,vW:e[2],vH:e[3]}}function z(n,e){var t=/[+-]?\d*\.?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,r=Y(w.pth(n)?n.totalLength:n,e)+"";return{original:r,numbers:r.match(t)?r.match(t).map(Number):[0],strings:w.str(n)||e?r.split(t):[]}}function A(n){return I(n?f(w.arr(n)?n.map(p):p(n)):[],function(n,e,t){return t.indexOf(n)===e})}function _(n){var t=A(n);return t.map(function(n,e){return{target:n,id:e,total:t.length,transforms:{list:W(n)}}})}function R(e){for(var t=I(f(e.map(function(n){return Object.keys(n)})),function(n){return w.key(n)}).reduce(function(n,e){return n.indexOf(e)<0&&n.push(e),n},[]),a={},n=0;n<t.length;n++)!function(n){var r=t[n];a[r]=e.map(function(n){var e,t={};for(e in n)w.key(e)?e==r&&(t.value=n[e]):t[e]=n[e];return t})}(n);return a}function J(n,e){var t,r=[],a=e.keyframes;for(t in e=a?D(R(a),e):e)w.key(t)&&r.push({name:t,tweens:function(n,t){var e,r=g(t),a=(/^spring/.test(r.easing)&&(r.duration=c(r.easing)),w.arr(n)&&(2===(e=n.length)&&!w.obj(n[0])?n={value:n}:w.fnc(t.duration)||(r.duration=t.duration/e)),w.arr(n)?n:[n]);return a.map(function(n,e){n=w.obj(n)&&!w.pth(n)?n:{value:n};return w.und(n.delay)&&(n.delay=e?0:t.delay),w.und(n.endDelay)&&(n.endDelay=e===a.length-1?t.endDelay:0),n}).map(function(n){return D(n,r)})}(e[t],n)});return r}function K(i,c){var s;return i.tweens.map(function(n){var n=function(n,e){var t,r={};for(t in n){var a=m(n[t],e);w.arr(a)&&1===(a=a.map(function(n){return m(n,e)})).length&&(a=a[0]),r[t]=a}return r.duration=parseFloat(r.duration),r.delay=parseFloat(r.delay),r}(n,c),e=n.value,t=w.arr(e)?e[1]:e,r=B(t),a=T(c.target,i.name,r,c),o=s?s.to.original:a,u=w.arr(e)?e[0]:o,a=B(u)||B(a),r=r||a;return w.und(t)&&(t=o),n.from=z(u,r),n.to=z(E(t,u),r),n.start=s?s.end:0,n.end=n.start+n.delay+n.duration+n.endDelay,n.easing=P(n.easing,n.duration),n.isPath=w.pth(e),n.isPathTargetInsideSVG=n.isPath&&w.svg(c.target),n.isColor=w.col(n.from.original),n.isColor&&(n.round=1),s=n})}var U={css:function(n,e,t){return n.style[e]=t},attribute:function(n,e,t){return n.setAttribute(e,t)},object:function(n,e,t){return n[e]=t},transform:function(n,e,t,r,a){var o;r.list.set(e,t),e!==r.last&&!a||(o="",r.list.forEach(function(n,e){o+=e+"("+n+") "}),n.style.transform=o)}};function nn(n,u){_(n).forEach(function(n){for(var e in u){var t=m(u[e],n),r=n.target,a=B(t),o=T(r,e,a,n),t=E(Y(t,a||B(o)),o),a=b(r,e);U[a](r,e,t,n.transforms,!0)}})}function en(n,e){return I(f(n.map(function(o){return e.map(function(n){var e,t,r=o,a=b(r.target,n.name);if(a)return t=(e=K(n,r))[e.length-1],{type:a,property:n.name,animatable:r,tweens:e,duration:t.end,delay:e[0].delay,endDelay:t.endDelay}})})),function(n){return!w.und(n)})}function tn(n,e){function t(n){return n.timelineOffset||0}var r=n.length,a={};return a.duration=r?Math.max.apply(Math,n.map(function(n){return t(n)+n.duration})):e.duration,a.delay=r?Math.min.apply(Math,n.map(function(n){return t(n)+n.delay})):e.delay,a.endDelay=r?a.duration-Math.max.apply(Math,n.map(function(n){return t(n)+n.duration-n.endDelay})):e.endDelay,a}var rn=0;var N,S=[],an=("undefined"!=typeof document&&document.addEventListener("visibilitychange",function(){L.suspendWhenDocumentHidden&&(n()?N=cancelAnimationFrame(N):(S.forEach(function(n){return n._onDocumentVisibility()}),an()))}),function(){!(N||n()&&L.suspendWhenDocumentHidden)&&0<S.length&&(N=requestAnimationFrame(on))});function on(n){for(var e=S.length,t=0;t<e;){var r=S[t];r.paused?(S.splice(t,1),e--):(r.tick(n),t++)}N=0<t?requestAnimationFrame(on):void 0}function n(){return document&&document.hidden}function L(n){var c,s=0,f=0,l=0,d=0,p=null;function h(n){var e=window.Promise&&new Promise(function(n){return p=n});return n.finished=e}e=x(i,n=n=void 0===n?{}:n),t=J(r=x(M,n),n),n=_(n.targets),r=tn(t=en(n,t),r),a=rn,rn++;var e,t,r,a,k=D(e,{id:a,children:[],animatables:n,animations:t,duration:r.duration,delay:r.delay,endDelay:r.endDelay});h(k);function g(){var n=k.direction;"alternate"!==n&&(k.direction="normal"!==n?"normal":"reverse"),k.reversed=!k.reversed,c.forEach(function(n){return n.reversed=k.reversed})}function m(n){return k.reversed?k.duration-n:n}function o(){s=0,f=m(k.currentTime)*(1/L.speed)}function v(n,e){e&&e.seek(n-e.timelineOffset)}function y(e){for(var n=0,t=k.animations,r=t.length;n<r;){for(var a=t[n],o=a.animatable,u=a.tweens,i=u.length-1,c=u[i],i=(i&&(c=I(u,function(n){return e<n.end})[0]||c),C(e-c.start-c.delay,0,c.duration)/c.duration),s=isNaN(i)?1:c.easing(i),f=c.to.strings,l=c.round,d=[],p=c.to.numbers.length,h=void 0,g=0;g<p;g++){var m=void 0,v=c.to.numbers[g],y=c.from.numbers[g]||0,m=c.isPath?function(e,t,n){function r(n){return e.el.getPointAtLength(1<=t+(n=void 0===n?0:n)?t+n:0)}var a=Q(e.el,e.svg),o=r(),u=r(-1),i=r(1),c=n?1:a.w/a.vW,s=n?1:a.h/a.vH;switch(e.property){case"x":return(o.x-a.x)*c;case"y":return(o.y-a.y)*s;case"angle":return 180*Math.atan2(i.y-u.y,i.x-u.x)/Math.PI}}(c.value,s*v,c.isPathTargetInsideSVG):y+s*(v-y);!l||c.isColor&&2<g||(m=Math.round(m*l)/l),d.push(m)}var b=f.length;if(b)for(var h=f[0],M=0;M<b;M++){f[M];var x=f[M+1],w=d[M];isNaN(w)||(h+=x?w+x:w+" ")}else h=d[0];U[a.type](o.target,a.property,h,o.transforms),a.currentValue=h,n++}}function b(n){k[n]&&!k.passThrough&&k[n](k)}function u(n){var e=k.duration,t=k.delay,r=e-k.endDelay,a=m(n);if(k.progress=C(a/e*100,0,100),k.reversePlayback=a<k.currentTime,c){var o=a;if(k.reversePlayback)for(var u=d;u--;)v(o,c[u]);else for(var i=0;i<d;i++)v(o,c[i])}!k.began&&0<k.currentTime&&(k.began=!0,b("begin")),!k.loopBegan&&0<k.currentTime&&(k.loopBegan=!0,b("loopBegin")),a<=t&&0!==k.currentTime&&y(0),(r<=a&&k.currentTime!==e||!e)&&y(e),t<a&&a<r?(k.changeBegan||(k.changeBegan=!0,k.changeCompleted=!1,b("changeBegin")),b("change"),y(a)):k.changeBegan&&(k.changeCompleted=!0,k.changeBegan=!1,b("changeComplete")),k.currentTime=C(a,0,e),k.began&&b("update"),e<=n&&(f=0,k.remaining&&!0!==k.remaining&&k.remaining--,k.remaining?(s=l,b("loopComplete"),k.loopBegan=!1,"alternate"===k.direction&&g()):(k.paused=!0,k.completed||(k.completed=!0,b("loopComplete"),b("complete"),!k.passThrough&&"Promise"in window&&(p(),h(k)))))}return k.reset=function(){var n=k.direction;k.passThrough=!1,k.currentTime=0,k.progress=0,k.paused=!0,k.began=!1,k.loopBegan=!1,k.changeBegan=!1,k.completed=!1,k.changeCompleted=!1,k.reversePlayback=!1,k.reversed="reverse"===n,k.remaining=k.loop,c=k.children;for(var e=d=c.length;e--;)k.children[e].reset();(k.reversed&&!0!==k.loop||"alternate"===n&&1===k.loop)&&k.remaining++,y(k.reversed?k.duration:0)},k._onDocumentVisibility=o,k.set=function(n,e){return nn(n,e),k},k.tick=function(n){u(((l=n)+(f-(s=s||l)))*L.speed)},k.seek=function(n){u(m(n))},k.pause=function(){k.paused=!0,o()},k.play=function(){k.paused&&(k.completed&&k.reset(),k.paused=!1,S.push(k),o(),an())},k.reverse=function(){g(),k.completed=!k.reversed,o()},k.restart=function(){k.reset(),k.play()},k.remove=function(n){cn(A(n),k)},k.reset(),k.autoplay&&k.play(),k}function un(n,e){for(var t=e.length;t--;)h(n,e[t].animatable.target)&&e.splice(t,1)}function cn(n,e){var t=e.animations,r=e.children;un(n,t);for(var a=r.length;a--;){var o=r[a],u=o.animations;un(n,u),u.length||o.children.length||r.splice(a,1)}t.length||r.length||e.pause()}return L.version="3.2.2",L.speed=1,L.suspendWhenDocumentHidden=!0,L.running=S,L.remove=function(n){for(var e=A(n),t=S.length;t--;)cn(e,S[t])},L.get=T,L.set=nn,L.convertPx=y,L.path=function(n,e){var t=w.str(n)?a(n)[0]:n,r=e||100;return function(n){return{property:n,el:t,svg:Q(t),totalLength:G(t)*(r/100)}}},L.setDashoffset=function(n){var e=G(n);return n.setAttribute("stroke-dasharray",e),e},L.stagger=function(n,e){var i=(e=void 0===e?{}:e).direction||"normal",c=e.easing?P(e.easing):null,s=e.grid,f=e.axis,l=e.from||0,d="first"===l,p="center"===l,h="last"===l,g=w.arr(n),m=g?parseFloat(n[0]):parseFloat(n),v=g?parseFloat(n[1]):0,y=B(g?n[1]:n)||0,b=e.start||0+(g?m:0),M=[],x=0;return function(n,e,t){if(d&&(l=0),p&&(l=(t-1)/2),h&&(l=t-1),!M.length){for(var r,a,o,u=0;u<t;u++)s?(r=p?(s[0]-1)/2:l%s[0],a=p?(s[1]-1)/2:Math.floor(l/s[0]),r=r-u%s[0],a=a-Math.floor(u/s[0]),o=Math.sqrt(r*r+a*a),"x"===f&&(o=-r),M.push(o="y"===f?-a:o)):M.push(Math.abs(l-u)),x=Math.max.apply(Math,M);c&&(M=M.map(function(n){return c(n/x)*x})),"reverse"===i&&(M=M.map(function(n){return f?n<0?-1*n:-n:Math.abs(x-n)}))}return b+(g?(v-m)/x:m)*(Math.round(100*M[e])/100)+y}},L.timeline=function(u){var i=L(u=void 0===u?{}:u);return i.duration=0,i.add=function(n,e){var t=S.indexOf(i),r=i.children;function a(n){n.passThrough=!0}-1<t&&S.splice(t,1);for(var o=0;o<r.length;o++)a(r[o]);t=D(n,x(M,u)),t.targets=t.targets||u.targets,n=i.duration,t.autoplay=!1,t.direction=i.direction,t.timelineOffset=w.und(e)?n:E(e,n),a(i),i.seek(t.timelineOffset),e=L(t),a(e),r.push(e),n=tn(r,u);return i.delay=n.delay,i.endDelay=n.endDelay,i.duration=n.duration,i.seek(0),i.reset(),i.autoplay&&i.play(),i},i},L.easing=P,L.penner=s,L.random=function(n,e){return Math.floor(Math.random()*(e-n+1))+n},L});
</script>
<script>
// ── Gauge constants (270° full-circle style) ──
const GAUGE_R   = 92;
const GAUGE_CX  = 130;
const GAUGE_CY  = 125;
const GAUGE_LEN = 2 * Math.PI * GAUGE_R * 270 / 360; // ≈ 433.5

// ── Gradient states ──
const GRADS = {
  clear:  { dark: ['#091828','#0F2844','#163A60'], light: ['#5A9EC8','#7ABAE0','#A4CFF0'] },
  cloudy: { dark: ['#161028','#261840','#3C2458'], light: ['#7858A8','#9878C0','#BCA0D8'] },
  storm:  { dark: ['#1A0808','#30100E','#4A1818'], light: ['#A83040','#C85060','#DC8090'] },
};

function getTheme() {
  const t = document.documentElement.getAttribute('data-theme');
  return (t === 'light') ? 'light' : 'dark';
}
function isCarbon() {
  return document.documentElement.getAttribute('data-vivid') === 'on';
}

// ── Carbon arc animations ────────────────────────────────────────────────────
function _setCarbonPulse(pct) {
  const low  = document.getElementById('arcLow');
  const med  = document.getElementById('arcMed');
  const high = document.getElementById('arcHigh');
  if (!low || !med || !high) return;
  const zone = pct < 50 ? 'low' : pct < 75 ? 'med' : 'high';
  // Clear ALL inline styles first — inline opacity/transition left by entry animation blocks keyframes
  [low, med, high].forEach(el => { el.style.opacity = ''; el.style.filter = ''; el.style.transition = ''; });
  low .classList.toggle('arc-pulse-low',  zone === 'low');
  med .classList.toggle('arc-pulse-med',  zone === 'med');
  high.classList.toggle('arc-pulse-high', zone === 'high');
}

function _carbonArcEntry(pct, callback) {
  const arcs = ['arcLow','arcMed','arcHigh'];
  // Hide all arcs instantly
  arcs.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.classList.remove('arc-pulse-low','arc-pulse-med','arc-pulse-high');
      el.style.transition = 'none';
      el.style.opacity = '0';
    }
  });
  // Staggered fade-in via anime.js
  const targets = arcs.map(id => document.getElementById(id)).filter(Boolean);
  anime({
    targets,
    opacity: [0, 1],
    duration: 850,
    delay: anime.stagger(280),
    easing: 'easeOutQuad',
    complete: () => { if (callback) callback(); }
  });
}

// Carbon zone arc lengths
const ARC_LOW_LEN  = GAUGE_LEN / 2;   // 0–50%  ≈ 216.8 px
const ARC_MED_LEN  = GAUGE_LEN / 4;   // 50–75% ≈ 108.4 px
const ARC_HIGH_LEN = GAUGE_LEN / 4;   // 75–100%≈ 108.4 px

// SF-style SVG icons for the theme toggle
const ICON_MOON = `<svg width="11" height="11" viewBox="0 0 11 11" fill="currentColor" style="flex-shrink:0;display:block">
  <path d="M9.2 7.6A5 5 0 0 1 3.4 1.8a4.3 4.3 0 1 0 5.8 5.8z"/>
</svg>`;
const ICON_SUN = `<svg width="12" height="12" viewBox="0 0 12 12" fill="none" style="flex-shrink:0;display:block">
  <circle cx="6" cy="6" r="2.4" fill="currentColor"/>
  <line x1="6" y1="0.5" x2="6" y2="2.2"   stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
  <line x1="6" y1="9.8" x2="6" y2="11.5"  stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
  <line x1="0.5" y1="6" x2="2.2" y2="6"   stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
  <line x1="9.8" y1="6" x2="11.5" y2="6"  stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
  <line x1="2.1" y1="2.1" x2="3.3" y2="3.3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
  <line x1="8.7" y1="8.7" x2="9.9" y2="9.9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
  <line x1="9.9" y1="2.1" x2="8.7" y2="3.3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
  <line x1="3.3" y1="8.7" x2="2.1" y2="9.9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
</svg>`;

function applyTheme(t) {
  // Normalise legacy 'system' → 'dark'
  const resolved = (t === 'light') ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', resolved);
  const btn = document.getElementById('themeToggle');
  if (btn) btn.innerHTML = resolved === 'dark'
    ? ICON_MOON + ' Dark'
    : ICON_SUN  + ' Light';
  if (window._d) renderAll(window._d);
}

function setGradient(pct) {
  const state = pct < 33 ? 'clear' : pct < 66 ? 'cloudy' : 'storm';
  const [g1,g2,g3] = GRADS[state][getTheme()];
  document.getElementById('app').style.background =
    `linear-gradient(175deg, ${g1} 0%, ${g2} 50%, ${g3} 100%)`;
}

// ── Arc helpers (semicircular gauge) ──
const NEEDLE_BW = 3.5;   // half-width of needle base
let _needleCurrent = 0;  // current animated position (pct)
let _needleRaf     = null;

function _drawNeedle(pct) {
  // 270° arc: start 225° (lower-left), sweep counterclockwise to 315° (lower-right)
  const angleRad = (225 - pct * 270 / 100) * Math.PI / 180;
  const nx = GAUGE_CX + GAUGE_R * Math.cos(angleRad);
  const ny = GAUGE_CY - GAUGE_R * Math.sin(angleRad);
  // perpendicular unit vector for base width
  const dx = Math.cos(angleRad), dy = -Math.sin(angleRad);
  const px = -dy, py = dx;
  document.getElementById('gaugeNeedle').setAttribute('points',
    `${nx.toFixed(1)},${ny.toFixed(1)} ` +
    `${(GAUGE_CX + px*NEEDLE_BW).toFixed(1)},${(GAUGE_CY + py*NEEDLE_BW).toFixed(1)} ` +
    `${(GAUGE_CX - px*NEEDLE_BW).toFixed(1)},${(GAUGE_CY - py*NEEDLE_BW).toFixed(1)}`);
}

// ── Idle pulse ──────────────────────────────────────────────────────────────
function startIdlePulse() {
  if (isCarbon()) return; // static arcs don't pulse
  const el = document.getElementById('gaugeFill');
  if (el) el.classList.add('idle-pulse');
}
function stopIdlePulse() {
  const el = document.getElementById('gaugeFill');
  if (el) el.classList.remove('idle-pulse');
}

// ── Spring needle (overshoot + settle) ──────────────────────────────────────
function _animateNeedle(target) {
  if (_needleRaf) { cancelAnimationFrame(_needleRaf); _needleRaf = null; }
  stopIdlePulse();
  const stiffness = 160, damping = 14, mass = 1;
  let pos = _needleCurrent, vel = 0, last = performance.now();
  (function step(now) {
    const dt = Math.min((now - last) / 1000, 0.048);
    last = now;
    const force = -stiffness * (pos - target) - damping * vel;
    vel += (force / mass) * dt;
    pos += vel * dt;
    _needleCurrent = pos;
    _drawNeedle(pos);
    if (Math.abs(pos - target) > 0.08 || Math.abs(vel) > 0.15) {
      _needleRaf = requestAnimationFrame(step);
    } else {
      _needleCurrent = target;
      _drawNeedle(target);
      _needleRaf = null;
      startIdlePulse();
    }
  })(performance.now());
}

function updateArc(pct) {
  const p   = Math.min(100, Math.max(0, pct));
  if (!isCarbon()) {
    const BIG = (GAUGE_LEN + 10).toFixed(2);
    const el = document.getElementById('gaugeFill');
    if (el) el.style.strokeDasharray = ((p / 100) * GAUGE_LEN).toFixed(2) + ' ' + BIG;
  } else {
    _setCarbonPulse(p);
  }
  // Carbon: colored arcs are permanently shown; needle still tracks value
  _animateNeedle(p);
}

function fmt(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return Math.round(n/1e3)+'K';
  return String(Math.round(n));
}
function fmtPct(n) { return Math.round(n*10)/10+'<span style="font-size:11px; vertical-align:top; line-height:1;">%</span>'; }

function statusText(pct) {
  if (pct <= 15)  return 'Fresh Start';
  if (pct <= 40)  return 'Comfortable';
  if (pct <= 60)  return 'Midway';
  if (pct <= 75)  return 'Getting There';
  if (pct <= 89)  return 'Running Low';
  if (pct < 100)  return 'Near Limit';
  return 'Maxed Out';
}


// ── Status crossfade ─────────────────────────────────────────────────────────
let _lastStatus = '';
let _statusAnim = null;
function setStatusAnimated(text) {
  const el = document.getElementById('heroStatus');
  if (!el || text === _lastStatus) return;
  _lastStatus = text;
  if (_statusAnim) { _statusAnim.pause(); _statusAnim = null; }
  _statusAnim = anime.timeline({ easing: 'easeOutQuad' })
    .add({ targets: el, opacity: 0, duration: 160 })
    .add({ targets: el, opacity: 1, duration: 220,
           begin: () => { el.textContent = text; } });
}

// ── Count-up with pop on finish ───────────────────────────────────────────────
function popEl(el) {
  if (!el) return;
  el.classList.remove('num-pop');
  void el.offsetWidth;          // force reflow to restart animation
  el.classList.add('num-pop');
  el.addEventListener('animationend', () => el.classList.remove('num-pop'), {once:true});
}

function countUp(el, target, duration) {
  if (el._animeInst) { el._animeInst.pause(); el._animeInst = null; }
  const start = parseFloat(el.textContent) || 0;
  // Use a plain object as a tween proxy — anime updates obj.val each frame
  const proxy = { val: start };
  el._animeInst = anime({
    targets: proxy,
    val: target,
    duration,
    round: 1,
    easing: 'easeOutCubic',
    update: () => {
      el.textContent = proxy.val;
      if (el.id === 'heroPctNum') _repositionPctSign();
    },
    complete: () => { el._animeInst = null; popEl(el); }
  });
}

// Reposition % sign so it sits just after the number, keeping number centered at x=130
function _repositionPctSign() {
  const numEl  = document.getElementById('heroPctNum');
  const signEl = document.getElementById('heroPctSign');
  if (!numEl || !signEl) return;
  try {
    const bbox = numEl.getBBox();
    // number is centered at x=130; right edge = 130 + bbox.width/2
    signEl.setAttribute('x', (130 + bbox.width / 2 + 2).toFixed(1));
  } catch(e) {}
}

function renderAll(d, animate) {
  const pct = Math.min(100, d.session_pct||0);

  const fillEl = document.getElementById('gaugeFill');

  if (animate) {
    _needleCurrent = 0;
    _drawNeedle(0);
    if (isCarbon()) {
      // Carbon: sequential arc light-up, needle sweeps in after short delay
      _carbonArcEntry(pct, () => {
        _setCarbonPulse(pct);
      });
      setTimeout(() => _animateNeedle(pct), 600);
    } else {
      // Aurora: anime.js sweeps the arc in from 0 → target
      if (fillEl) {
        fillEl.classList.remove('arc-glow');
        const BIG = (GAUGE_LEN + 10).toFixed(2);
        fillEl.setAttribute('stroke-dasharray', '0 ' + BIG);
        const targetLen = ((pct / 100) * GAUGE_LEN).toFixed(2);
        anime({
          targets: fillEl,
          strokeDasharray: targetLen + ' ' + BIG,
          duration: 1100,
          easing: 'easeOutCubic',
          complete: () => { if (pct > 0) fillEl.classList.add('arc-glow'); }
        });
        _animateNeedle(pct);
      }
    }
  } else {
    updateArc(pct);
    if (!isCarbon()) fillEl?.classList.toggle('arc-glow', pct > 0);
  }

  const heroPctEl = document.getElementById('heroPctNum');
  const hasDataEarly = !!d.has_data;  // needed before the has_data block below

  if (!hasDataEarly) {
    // Empty state — blank all numbers
    heroPctEl.textContent = '--';
    document.getElementById('heroPctSign').style.visibility = 'hidden';
    _repositionPctSign();
    setStatusAnimated('—');
    document.getElementById('updated').textContent = d.updated||'—';
    document.getElementById('sessPctNum').textContent = '--';
    document.getElementById('weekPctNum').textContent = '--';
    document.getElementById('sessResetTime').textContent = 'resets --';
    document.getElementById('weekResetTime').textContent = 'resets --';
    document.getElementById('sessCd').textContent = '--';
    document.getElementById('weekCd').textContent = '--';
  } else {
    document.getElementById('heroPctSign').style.visibility = '';
    if (animate) {
      countUp(heroPctEl, Math.round(pct), 1400);
      // Card shimmer on open
      const shimmer = document.getElementById('statsShimmer');
      if (shimmer) {
        shimmer.classList.remove('run');
        void shimmer.offsetWidth;
        shimmer.classList.add('run');
      }
    } else {
      heroPctEl.textContent = Math.round(pct);
      _repositionPctSign();
    }
    setStatusAnimated(statusText(pct));
    document.getElementById('updated').textContent    = d.updated||'—';

    const sessPctNum = document.getElementById('sessPctNum');
    const weekPctNum = document.getElementById('weekPctNum');
    if (animate) {
      countUp(sessPctNum, Math.round(d.session_pct||0), 1200);
      countUp(weekPctNum, Math.round(d.week_pct||0), 1200);
    } else {
      sessPctNum.textContent = Math.round(d.session_pct||0);
      weekPctNum.textContent = Math.round(d.week_pct||0);
    }
    document.getElementById('sessResetTime').textContent = 'resets '+(d.session_reset||'--');
    document.getElementById('weekResetTime').textContent = 'resets '+(d.week_reset||'--');
    startCountdown('sess', d.session_reset_epoch, 'sessCd');
    startCountdown('week', d.week_reset_epoch,    'weekCd');
  }

  // Cookie expiry warning badge
  const warn = document.getElementById('cookieWarn');
  if (warn) warn.style.display = d.cookie_expired ? 'inline' : 'none';

  // Expired state — banner, stale gauge, last synced
  const expired = !!d.cookie_expired;
  document.documentElement.setAttribute('data-expired', expired ? 'on' : 'off');
  const lastSyncedEl = document.getElementById('lastSyncedTime');
  if (lastSyncedEl) lastSyncedEl.textContent = d.updated || '—';

  // Auto-open cookie form when expired (only on first detection)
  if (expired && !window._expiredFormOpened) {
    window._expiredFormOpened = true;
    setTimeout(() => {
      const cookieToggleBtn = document.getElementById('cookieToggle');
      if (cookieToggleBtn) cookieToggleBtn.click();
    }, 600);
  }
  if (!expired) window._expiredFormOpened = false;

  // Onboarding overlay — once dismissed in this session never re-show,
  // even if a stale payload with first_launch:true arrives from a background tick
  const firstLaunch = !!d.first_launch && !window._onboardingDone;
  document.documentElement.setAttribute('data-onboarding', firstLaunch ? 'on' : 'off');

  // Empty state (only shown when past onboarding and no data)
  const hasData = !!d.has_data;
  document.documentElement.setAttribute('data-empty', (!hasData && !firstLaunch) ? 'on' : 'off');
  const emptyMsg = document.getElementById('emptyMsg');
  const emptySub = document.getElementById('emptySub');
  if (emptyMsg && emptySub && !hasData) {
    if (!d.claude_installed) {
      emptyMsg.textContent = 'Claude Code not detected';
      emptySub.textContent = 'Install the Claude Code CLI to track usage';
    } else {
      emptyMsg.textContent = 'No usage this month';
      emptySub.textContent = 'Activity will appear after using Claude Code';
    }
  }

  renderEfficiency(d);
  if (d.daily_7) renderWeekChart(d.daily_7);
  renderProjectBreakdown(d);
  renderHeatmap(d);
  applyModules(d);
}

// ── Modules: show/hide optional cards ─────────────────────────────────────────
function applyModules(d) {
  if (!d.modules) return;
  const app = document.getElementById('app');
  const cards = [
    { id: 'effCard',       key: 'eff'     },
    { id: 'weekChartCard', key: 'week'    },
    { id: 'projectCard',   key: 'project' },
    { id: 'heatmapCard',   key: 'heatmap' },
  ];
  let delta = 0;

  for (const {id, key} of cards) {
    const card = document.getElementById(id);
    if (!card) continue;
    const shouldShow = !!d.modules[key];
    const isVisible  = card.style.display !== 'none';
    if (shouldShow === isVisible) continue; // already correct — no-op

    const style   = window.getComputedStyle(card);
    const marginT = parseFloat(style.marginTop) || 0;

    if (!shouldShow) {
      // Measure full height (incl. any expanded body) BEFORE collapsing
      const totalH = card.offsetHeight + marginT;
      // Collapse expanded body & sync closure state (instant, no animation)
      if (id === 'effCard'       && window._resetEff)     window._resetEff();
      if (id === 'weekChartCard' && window._resetWeek)    window._resetWeek();
      if (id === 'projectCard'   && window._resetProject) window._resetProject();
      if (id === 'heatmapCard'   && window._resetHeatmap) window._resetHeatmap();
      card.style.display = 'none';
      delta -= totalH;
    } else {
      // Re-enable: show card, ensure it starts collapsed
      card.style.display = '';
      if (id === 'effCard'       && window._resetEff)     window._resetEff();
      if (id === 'weekChartCard' && window._resetWeek)    window._resetWeek();
      if (id === 'projectCard'   && window._resetProject) window._resetProject();
      if (id === 'heatmapCard'   && window._resetHeatmap) window._resetHeatmap();
      // Reset cascade so it plays again when card becomes visible
      if (id === 'effCard') { window._effReady = false; window._effZone = undefined; }
      // Measure collapsed height after showing
      const totalH = card.offsetHeight + marginT;
      delta += totalH;
    }
  }

  if (delta !== 0) {
    const newH = (parseInt(app.style.height) || H_BASE) + delta;
    app.style.height = newH + 'px';
    window.webkit.messageHandlers.cm.postMessage({action: 'resize', h: newH});
  }
}

// ── Prompt Efficiency (leverage-based) ───────────────────────────────────────
const EFF_ZONES  = ['Sharp','Focused','Moderate','Verbose','Scattered'];
const EFF_COLORS = ['#52BAFF','#8882F0','#A87CF5','#D46090','#F26280'];
// Thresholds are minimum ratios per zone — higher ratio = better = lower zone index
const EFF_BOUNDS = [0.30, 0.18, 0.10, 0.05, 0];

function effZone(ratio) {
  for (let i = 0; i < EFF_BOUNDS.length; i++)
    if (ratio >= EFF_BOUNDS[i]) return i;
  return 4;
}

function renderEfficiency(d) {
  const rate = (d.eff_rate !== null && d.eff_rate !== undefined) ? d.eff_rate : null;
  const elMin = d.eff_min || 0;

  const today = new Date().toISOString().slice(0, 10);
  let hist = {};
  try { hist = JSON.parse(localStorage.getItem('effHistV2') || '{}'); } catch(e) {}

  if (rate !== null) {
    const prev = hist[today] || {avg: rate, n: 0};
    const n = prev.n + 1;
    hist[today] = {avg: (prev.avg * prev.n + rate) / n, n};
    try { localStorage.setItem('effHistV2', JSON.stringify(hist)); } catch(e) {}
  }

  const todayRate = hist[today] ? hist[today].avg : null;
  const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - 30);
  let mSum = 0, mN = 0;
  for (const [k, v] of Object.entries(hist)) {
    if (new Date(k) >= cutoff && v.avg) { mSum += v.avg * v.n; mN += v.n; }
  }
  const monthRate = mN > 0 ? mSum / mN : null;

  const zi = rate !== null ? effZone(rate) : -1;
  const doCascade = (rate !== null && !window._effReady);

  if (doCascade) {
    // ── Cascade: segments fly in left-to-right, each settles to its final opacity ──
    const STAGGER = 120, DUR = 500;
    window._cascadeRunning = true;
    for (let i = 0; i < 5; i++) {
      const seg = document.getElementById('es' + i);
      if (!seg) continue;
      seg.classList.remove('on');
      seg.style.animation = 'none';
      seg.style.opacity   = '0';
      seg.style.transform = '';
    }
    for (let i = 0; i < 5; i++) {
      const seg = document.getElementById('es' + i);
      if (!seg) continue;
      const isActive = (i === zi);
      const kf = isActive ? 'seg-cascade-active' : 'seg-cascade-dim';
      const isLast = (i === 4);
      ;(function(s, active, keyframe, last) {
        setTimeout(() => {
          s.style.animation = keyframe + ' ' + DUR + 'ms cubic-bezier(.22,.68,0,1.2) forwards';
          setTimeout(() => {
            // Disable transition FIRST so removing inline opacity/animation
            // doesn't trigger a CSS opacity transition from 0 to final value.
            s.style.transition = 'none';
            s.classList.toggle('on', active);
            s.style.opacity   = '';
            s.style.animation = '';
            s.style.transform = '';
            void s.offsetWidth; // flush — commit styles before re-enabling transition
            s.style.transition = '';
            if (last) window._cascadeRunning = false;
          }, DUR + 20);
        }, i * STAGGER);
      })(seg, isActive, kf, isLast);
    }
  } else if (!window._cascadeRunning) {
    // ── Zone change or no change — only run if cascade isn't mid-flight ──
    for (let i = 0; i < 5; i++) {
      const seg = document.getElementById('es' + i);
      if (seg) seg.classList.toggle('on', i === zi);
    }
  }

  // Labels always snap (no animation needed)
  for (let i = 0; i < 5; i++) {
    const lbl = document.getElementById('el' + i);
    if (lbl) lbl.classList.toggle('on', i === zi);
  }

  window._effZone = zi;

  const metaEl = document.getElementById('effMeta');
  if (metaEl) {
    if (rate !== null) {
      const msgLabel = elMin <= 1 ? '1 message' : elMin + ' messages';
      metaEl.textContent = rate.toFixed(2) + '× output/input  ·  ' + msgLabel;
    } else {
      metaEl.textContent = 'collecting data…';
    }
  }

  function setHistCol(id, r) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = r !== null ? EFF_ZONES[effZone(r)] : '--';
  }
  setHistCol('effTodayVal', todayRate);
  setHistCol('effMonthVal', monthRate);

  if (doCascade) {
    window._effReady = true;
    const sh = document.getElementById('effShimmer');
    if (sh) { sh.classList.remove('run'); void sh.offsetWidth; sh.classList.add('run'); }
  }
}

// ── (leverage card removed — efficiency card now uses output/input ratio) ─────
function renderLeverage(d) {
  const lev = d.leverage;
  if (!lev) return;

  const ratio  = lev.ratio;
  const delta  = lev.delta;
  const daily7 = lev.daily_7 || [];

  // Number
  const numEl = document.getElementById('levNum');
  if (numEl) numEl.textContent = ratio !== null ? ratio.toFixed(2) + '×' : '--';

  // Badge
  const badgeEl = document.getElementById('levBadge');
  if (badgeEl) {
    let label = '--', cls = '';
    if (ratio !== null) {
      if      (ratio >= 0.20) { label = 'High';     cls = 'high';     }
      else if (ratio >= 0.06) { label = 'Balanced'; cls = 'balanced'; }
      else                    { label = 'Low';       cls = 'low';      }
    }
    badgeEl.textContent = label;
    badgeEl.className = 'lev-badge' + (cls ? ' ' + cls : '');
  }

  // Delta
  const deltaEl = document.getElementById('levDelta');
  if (deltaEl) {
    if (delta !== null && delta !== undefined) {
      const sign = delta >= 0 ? '↑ +' : '↓ ';
      deltaEl.textContent = sign + Math.abs(delta).toFixed(2) + ' vs last wk';
      deltaEl.className = 'lev-delta ' + (delta >= 0 ? 'up' : 'down');
    } else {
      deltaEl.textContent = '';
      deltaEl.className = 'lev-delta';
    }
  }

  _renderLevSpark(daily7);
}

function _renderLevSpark(daily7) {
  const svg      = document.getElementById('levSpark');
  const labelsEl = document.getElementById('levSparkLabels');
  if (!svg || !labelsEl) return;

  while (svg.firstChild) svg.removeChild(svg.firstChild);
  labelsEl.innerHTML = '';

  const W = 272, H = 52, PAD = 6;
  const n = daily7.length;
  if (!n) return;

  const ns   = 'http://www.w3.org/2000/svg';
  const vals = daily7.map(d => d.ratio).filter(v => v !== null && v !== undefined);

  if (!vals.length) {
    // No data yet — draw a quiet dashed baseline
    const line = document.createElementNS(ns, 'line');
    line.setAttribute('x1', PAD);   line.setAttribute('y1', H / 2);
    line.setAttribute('x2', W-PAD); line.setAttribute('y2', H / 2);
    line.setAttribute('stroke', 'rgba(255,255,255,0.12)');
    line.setAttribute('stroke-width', '1');
    line.setAttribute('stroke-dasharray', '3 4');
    svg.appendChild(line);
  } else {
    const minV  = Math.max(0, Math.min(...vals) * 0.80);
    const maxV  = Math.max(...vals) * 1.20 || 0.01;

    // Centre each dot over its day-label column (matches the flex label layout exactly)
    const toX = i => (i + 0.5) * W / n;
    const toY = v => H - PAD - ((v - minV) / (maxV - minV)) * (H - PAD * 2);

    // Gradient defs
    const defs = document.createElementNS(ns, 'defs');
    const grad = document.createElementNS(ns, 'linearGradient');
    grad.setAttribute('id', 'levSparkGrad');
    grad.setAttribute('gradientUnits', 'userSpaceOnUse');
    grad.setAttribute('x1', '0'); grad.setAttribute('y1', '0');
    grad.setAttribute('x2', '0'); grad.setAttribute('y2', H);
    const s1 = document.createElementNS(ns, 'stop');
    s1.setAttribute('offset', '0%');   s1.setAttribute('stop-color', 'rgba(168,124,245,0.30)');
    const s2 = document.createElementNS(ns, 'stop');
    s2.setAttribute('offset', '100%'); s2.setAttribute('stop-color', 'rgba(168,124,245,0.00)');
    grad.appendChild(s1); grad.appendChild(s2);
    defs.appendChild(grad);
    svg.appendChild(defs);

    // Collect all non-null points — connect them in one continuous path
    // (null days simply have no dot; the line bridges across them)
    const pts = daily7
      .map((dv, i) => dv.ratio !== null && dv.ratio !== undefined
        ? {x: toX(i), y: toY(dv.ratio), today: dv.today}
        : null)
      .filter(p => p !== null);

    if (pts.length >= 1) {
      // Area fill — close down to baseline
      const aD = [`M ${pts[0].x.toFixed(1)} ${H}`];
      pts.forEach(p => aD.push(`L ${p.x.toFixed(1)} ${p.y.toFixed(1)}`));
      aD.push(`L ${pts[pts.length-1].x.toFixed(1)} ${H} Z`);
      const area = document.createElementNS(ns, 'path');
      area.setAttribute('d', aD.join(' '));
      area.setAttribute('fill', 'url(#levSparkGrad)');
      svg.appendChild(area);

      // Line
      if (pts.length >= 2) {
        const lD = [`M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`];
        pts.slice(1).forEach(p => lD.push(`L ${p.x.toFixed(1)} ${p.y.toFixed(1)}`));
        const line = document.createElementNS(ns, 'path');
        line.setAttribute('d', lD.join(' '));
        line.setAttribute('fill', 'none');
        line.setAttribute('stroke', 'rgba(168,124,245,0.80)');
        line.setAttribute('stroke-width', '1.5');
        line.setAttribute('stroke-linecap', 'round');
        line.setAttribute('stroke-linejoin', 'round');
        svg.appendChild(line);
      }
    }

    // Dots — filled for days with data, dim hollow for empty days
    const midY = toY((minV + maxV) / 2);
    daily7.forEach((dv, i) => {
      const x = toX(i).toFixed(1);
      const circle = document.createElementNS(ns, 'circle');
      circle.setAttribute('cx', x);
      if (dv.ratio !== null && dv.ratio !== undefined) {
        const y = toY(dv.ratio).toFixed(1);
        circle.setAttribute('cy', y);
        circle.setAttribute('r',  dv.today ? '3.5' : '2.8');
        circle.setAttribute('fill', dv.today ? '#A87CF5' : 'rgba(168,124,245,0.70)');
        if (dv.today) {
          circle.setAttribute('stroke', 'rgba(255,255,255,0.40)');
          circle.setAttribute('stroke-width', '1.5');
        }
      } else {
        // No data — small hollow dot on the midline
        circle.setAttribute('cy', (H - PAD).toFixed(1));
        circle.setAttribute('r',  '2');
        circle.setAttribute('fill', 'none');
        circle.setAttribute('stroke', 'rgba(255,255,255,0.15)');
        circle.setAttribute('stroke-width', '1');
      }
      svg.appendChild(circle);
    });
  }

  // Day labels
  daily7.forEach(dv => {
    const lbl = document.createElement('div');
    lbl.className   = 'lev-spark-lbl' + (dv.today ? ' today' : '');
    lbl.textContent = dv.day;
    labelsEl.appendChild(lbl);
  });
}

// ── 7-Day usage chart (segmented LED-style bars) ─────────────────────────────
window._weekExpanded  = true;   // kept in sync by toggle IIFE
window._weekChartReady = false; // animate once per open

const N_SEGS = 10;
// Segment colours: index 0 = bottom, index 9 = top
const SEG_COLORS_AURORA = [
  '#52BAFF','#6EB0F7','#7A8DF0','#8E80F0',
  '#A87CF5','#B876E0','#C470C0','#C46490',
  '#E05078','#F26280'
];
const SEG_COLORS_CARBON = [
  '#FF6B6B','#FF7C5C','#FF9045','#FFA830',
  '#FFC857','#D4C96A','#7ECBA4','#45C4B8',
  '#36C5C5','#4ECDC4'
];

function animateWeekBars() {
  const cols = document.querySelectorAll('#weekChart .week-bar-col');
  // Collect all segments in column-major, bottom-to-top order
  const allSegs = [];
  cols.forEach((col, ci) => {
    const active = [...col.querySelectorAll('.wseg.won')].reverse();
    allSegs.push(...active);
  });
  if (!allSegs.length) return;
  anime({
    targets: allSegs,
    opacity: [0, 1],
    duration: 320,
    delay: anime.stagger(28, { easing: 'easeOutQuad' }),
    easing: 'easeOutCubic'
  });
}

function renderWeekChart(daily7) {
  if (!daily7 || !daily7.length) return;
  const chart    = document.getElementById('weekChart');
  const labelsEl = document.getElementById('weekLabels');
  if (!chart || !labelsEl) return;

  // Fixed absolute scale: WEEK_LIMIT ÷ 7 = full bar.
  // A full bar = you used your entire daily share of the weekly budget.
  const DAILY_SCALE = 179_000_000 / 7;  // ≈ 25.6M tokens
  chart.innerHTML = ''; labelsEl.innerHTML = '';

  const SEG_COLORS = isCarbon() ? SEG_COLORS_CARBON : SEG_COLORS_AURORA;
  daily7.forEach((d, ci) => {
    const isToday  = !!d.today;
    const active   = d.tokens > 0 ? Math.min(N_SEGS, Math.max(1, Math.round(d.tokens / DAILY_SCALE * N_SEGS))) : 0;
    const willAnim = window._weekExpanded && !window._weekChartReady;

    const col = document.createElement('div');
    col.className = 'week-bar-col';

    // Render top → bottom in DOM (seg index N_SEGS-1 … 0)
    for (let s = N_SEGS - 1; s >= 0; s--) {
      const seg = document.createElement('div');
      const on  = s < active;
      seg.className = 'wseg ' + (on ? 'won' : 'wdim');
      seg.style.background = SEG_COLORS[s];
      if (on && isToday) seg.style.boxShadow = '0 0 4px ' + SEG_COLORS[s] + 'AA';
      // Start invisible if we're about to animate
      if (on && willAnim) seg.style.opacity = '0';
      col.appendChild(seg);
    }
    chart.appendChild(col);

    const lbl = document.createElement('div');
    lbl.className   = 'week-lbl' + (isToday ? ' today' : '');
    lbl.textContent = d.day;
    labelsEl.appendChild(lbl);
  });

  if (!window._weekChartReady && window._weekExpanded) {
    window._weekChartReady = true;
    // Shimmer the card
    const sh = document.getElementById('weekChartShimmer');
    if (sh) { sh.classList.remove('run'); void sh.offsetWidth; sh.classList.add('run'); }
    animateWeekBars();
  }
}

// ── Project Breakdown ─────────────────────────────────────────────────────────
window._projMode = 'session';

function renderProjectBreakdown(d) {
  if (!d.projects) return;
  const rowsEl  = document.getElementById('projRows');
  const emptyEl = document.getElementById('projEmpty');
  if (!rowsEl) return;

  const data = d.projects[window._projMode] || [];
  rowsEl.innerHTML = '';
  if (emptyEl) emptyEl.style.display = data.length ? 'none' : 'block';

  const COLORS = isCarbon()
    ? ['#AAD7FE','#ECB967','#FF654D']
    : ['#52BAFF','#A87CF5','#F26280'];
  data.forEach((proj, i) => {
    const row = document.createElement('div');
    row.className = 'proj-row';
    row.innerHTML =
      `<span class="proj-name" title="${proj.name}">${proj.name}</span>` +
      `<div class="proj-bar-wrap"><div class="proj-bar" data-pct="${proj.pct}"` +
      ` style="background:${COLORS[i % COLORS.length]}"></div></div>` +
      `<span class="proj-val">${fmt(proj.tokens)}</span>` +
      `<span class="proj-pct">${Math.round(proj.pct)}%</span>`;
    rowsEl.appendChild(row);
  });
  // Animate bars with anime.js spring, then snap height
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const bars = [...rowsEl.querySelectorAll('.proj-bar')];
    bars.forEach(b => { b.style.width = '0%'; });
    anime({
      targets: bars,
      width: (el) => (el.dataset.pct || 0) + '%',
      duration: 700,
      delay: anime.stagger(80),
      easing: 'spring(1, 80, 12, 0)',
      complete: () => { if (window._updateProjH) window._updateProjH(); }
    });
    if (window._updateProjH) window._updateProjH();
  }));
}

// ── Hourly Heatmap ─────────────────────────────────────────────────────────────
function renderHeatmap(d) {
  if (!d.heatmap) return;
  const gridEl = document.getElementById('heatmapGrid');
  const axisEl = document.getElementById('heatmapAxis');
  if (!gridEl) return;

  gridEl.innerHTML = '';

  const grid   = d.heatmap; // 7×24 matrix
  const maxVal = Math.max(...grid.flat(), 1);
  const vivid  = isCarbon();
  const DOW    = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

  grid.forEach((row, di) => {
    const rowEl = document.createElement('div');
    rowEl.className = 'heatmap-row';

    const lbl = document.createElement('span');
    lbl.className   = 'heatmap-dow';
    lbl.textContent = DOW[di];
    rowEl.appendChild(lbl);

    const cellsEl = document.createElement('div');
    cellsEl.className = 'heatmap-cells';

    row.forEach((val, hi) => {
      const cell = document.createElement('div');
      cell.className = 'heatmap-cell';
      if (val > 0) {
        const intensity = val / maxVal;
        const alpha = Math.max(0.12, Math.min(1.0, intensity * 0.88 + 0.12));
        if (vivid) {
          // Carbon mode — warm amber
          cell.style.background = `rgba(255,184,0,${(alpha * 0.9).toFixed(2)})`;
        } else {
          // Default — blue→violet sweep across hours
          const t = hi / 23;
          const r = Math.round(82  + (168 - 82)  * t);
          const g = Math.round(186 + (124 - 186) * t);
          const b = Math.round(255 + (245 - 255) * t);
          cell.style.background = `rgba(${r},${g},${b},${alpha.toFixed(2)})`;
        }
        const dayName = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][di];
        const hr = hi % 12 || 12;
        const ap = hi < 12 ? 'AM' : 'PM';
        cell.title = `${dayName} ${hr}${ap} — ${fmt(val)} tokens`;
      }
      cellsEl.appendChild(cell);
    });

    rowEl.appendChild(cellsEl);
    gridEl.appendChild(rowEl);
  });

  // Hour-axis labels: 12A, 6A, 12P, 6P
  if (axisEl) {
    axisEl.innerHTML = '';
    [{h:0,t:'12A'},{h:6,t:'6A'},{h:12,t:'12P'},{h:18,t:'6P'}].forEach(({h,t}) => {
      const lbl = document.createElement('span');
      lbl.className   = 'heatmap-axis-lbl';
      lbl.textContent = t;
      lbl.style.left  = (h / 24 * 100) + '%';
      axisEl.appendChild(lbl);
    });
  }

  if (window._updateHeatmapH) window._updateHeatmapH();
}

// ── Countdowns ──
const _cdIntervals = {};
const _cdEpochs    = {};

function fmtCountdown(epochMs, withSecs) {
  const diff = Math.max(0, epochMs - Date.now());
  const total = Math.floor(diff / 1000);
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, '0');
  if (d > 0) return d+'d '+h+'h '+m+'m';
  if (withSecs) {
    if (h > 0) return h+'h '+m+'m '+ss+'s';
    return m+'m '+ss+'s';
  }
  if (h > 0) return h+'h '+m+'m';
  return m+'m';
}

function startCountdown(key, epochMs, cdElId) {
  if (epochMs) _cdEpochs[key] = epochMs;
  if (!_cdEpochs[key]) return;
  if (_cdIntervals[key]) clearInterval(_cdIntervals[key]);
  const withSecs = (key === 'sess');
  const tick = () => {
    const el = document.getElementById(cdElId);
    if (el) el.textContent = fmtCountdown(_cdEpochs[key], withSecs);
  };
  tick();
  _cdIntervals[key] = setInterval(tick, withSecs ? 1000 : 30000);
}

window._animateNext = true;   // animate on first data inject after each open

window.updateData = function(raw) {
  const d = typeof raw==='string' ? JSON.parse(raw) : raw;
  window._d = d;
  if (d.theme) applyTheme(d.theme);
  const anim = d._animate || window._animateNext;
  window._animateNext = false;
  renderAll(d, anim);
};


document.getElementById('themeToggle').addEventListener('click', () => {
  const next = getTheme() === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  window.webkit.messageHandlers.cm.postMessage({action:'theme', value: next});
});

// ── Vivid color mode ──────────────────────────────────────────────────────────
(function() {
  let vivid = false;
  try { vivid = localStorage.getItem('vividMode') === '1'; } catch(e) {}
  const btn = document.getElementById('vividToggle');
  function applyVivid(on) {
    document.documentElement.setAttribute('data-vivid', on ? 'on' : 'off');
    if (btn) btn.classList.toggle('active', on);
    if (window._d) {
      const pct = Math.min(100, window._d.session_pct || 0);
      if (on) {
        // Switching to Carbon: reset gradient fill, run arc entry + pulse, animate needle
        const fe = document.getElementById('gaugeFill');
        if (fe) { fe.classList.remove('arc-glow'); fe.style.strokeDasharray = ''; }
        _carbonArcEntry(pct, () => { _setCarbonPulse(pct); });
        setTimeout(() => _animateNeedle(pct), 500);
        // Re-render data modules with Carbon palette
        if (window._d) {
          if (window._d.daily_7) renderWeekChart(window._d.daily_7);
          renderProjectBreakdown(window._d);
          renderHeatmap(window._d);
        }
      } else {
        // Switching to Default: clear carbon pulse classes, animate gradient fill in
        ['arcLow','arcMed','arcHigh'].forEach(id => {
          const el = document.getElementById(id);
          if (el) { el.classList.remove('arc-pulse-low','arc-pulse-med','arc-pulse-high','arc-entry'); el.style.opacity = ''; el.style.filter = ''; }
        });
        renderAll(window._d, true);
      }
    }
  }
  applyVivid(vivid);
  if (btn) btn.addEventListener('click', () => {
    vivid = !vivid;
    try { localStorage.setItem('vividMode', vivid ? '1' : '0'); } catch(e) {}
    applyVivid(vivid);
  });
})();

document.getElementById('refreshNow').addEventListener('click', () => {
  const btn = document.getElementById('refreshNow');
  btn.classList.add('spinning');
  btn.addEventListener('animationend', () => btn.classList.remove('spinning'), {once:true});
  window._animateNext    = true;      // replay all animations on next data inject
  window._effReady       = false;     // replay efficiency shimmer + cascade too
  window._effZone        = undefined; // force cascade path on next render
  window._weekChartReady = false;     // replay 7-day bar animation
  window.webkit.messageHandlers.cm.postMessage({action:'refresh', iv: window._d ? (window._d.interval||60) : 60});
});



// ── Onboarding CTA ──
document.getElementById('obCta').addEventListener('click', () => {
  // Lock out any stale payload from re-showing the overlay
  window._onboardingDone = true;
  // Dismiss overlay
  document.documentElement.setAttribute('data-onboarding', 'off');
  window.webkit.messageHandlers.cm.postMessage({action: 'onboarding_done'});

  // Open info tooltip so user sees the step-by-step guide
  setTimeout(() => {
    const infoBtn = document.getElementById('infoToggle');
    const infoPanel = document.getElementById('infoPanel');
    if (infoPanel && !infoPanel.classList.contains('open')) {
      if (infoBtn) infoBtn.click();
    }
  }, 120);

  // Open cookie form directly (no .click() — avoids bubbling to app click-outside handler)
  setTimeout(() => {
    const cookieForm = document.getElementById('cookieForm');
    if (cookieForm && !cookieForm.classList.contains('open') && window._openCookieForm) {
      window._openCookieForm();
    }
    // Add pulsing highlight after form has animated open
    setTimeout(() => {
      const input = document.getElementById('cookieInput');
      if (input) input.classList.add('ob-highlight');
    }, 300);
  }, 200);
});

// ── Shared height constants (used by cookie form + efficiency toggle) ──
const H_BASE   = 593;   // efficiency collapsed by default
const H_COOKIE = 663;   // H_BASE + 70 for cookie form

// Ensure getAppH() always has a reliable baseline
document.getElementById('app').style.height = H_BASE + 'px';

// ── Expired banner → jumps to cookie form ──
document.getElementById('expiredUpdateBtn').addEventListener('click', () => {
  const btn = document.getElementById('cookieToggle');
  if (btn) {
    btn.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    btn.click();
  }
});

// ── Cookie key UI ──
(function() {

  const toggle = document.getElementById('cookieToggle');
  const form   = document.getElementById('cookieForm');
  const input  = document.getElementById('cookieInput');
  const save   = document.getElementById('cookieSave');
  const cancel = document.getElementById('cookieCancel');
  const status = document.getElementById('cookieStatus');
  const app    = document.getElementById('app');

  const FORM_DELTA = H_COOKIE - H_BASE;  // height added by the cookie form (70px)

  function openForm() {
    form.classList.add('open');
    toggle.classList.remove('active');
    input.classList.remove('cookie-typed');
    const newH = (parseInt(app.style.height) || H_BASE) + FORM_DELTA;
    app.style.height = newH + 'px';
    window.webkit.messageHandlers.cm.postMessage({action:'resize', h: newH});
    setTimeout(() => input.focus(), 240);
  }

  function closeForm() {
    form.classList.remove('open');
    toggle.classList.remove('active');
    input.classList.remove('cookie-typed');
    input.classList.remove('ob-highlight');
    const newH = (parseInt(app.style.height) || H_BASE) - FORM_DELTA;
    app.style.height = newH + 'px';
    window.webkit.messageHandlers.cm.postMessage({action:'resize', h: newH});
    input.value = ''; status.textContent = ''; status.className = 'cookie-status';
  }

  // Amber border + onboarding highlight both clear once the user starts typing
  input.addEventListener('input', () => {
    input.classList.add('cookie-typed');
    input.classList.remove('ob-highlight');
  });

  // Expose openForm globally so onboarding can call it without dispatching
  // a bubbling click event that would trigger click-outside handlers
  window._openCookieForm = openForm;

  toggle.addEventListener('click', () => {
    form.classList.contains('open') ? closeForm() : openForm();
  });

  cancel.addEventListener('click', closeForm);

  save.addEventListener('click', () => {
    const val = input.value.trim();
    if (!val) { status.textContent = 'Paste your sessionKey first.'; status.className = 'cookie-status err'; return; }
    status.textContent = 'Saving…'; status.className = 'cookie-status';
    window.webkit.messageHandlers.cm.postMessage({action:'cookie', value: val});
  });

  window.cookieSaved = function(ok) {
    if (ok) {
      status.textContent = '✓ Saved — fetching live data…';
      status.className = 'cookie-status ok';
      setTimeout(closeForm, 1800);
    } else {
      status.textContent = '✗ Invalid or expired — try again.';
      status.className = 'cookie-status err';
    }
  };
})();

// ── Shared: close all tooltips/panels ──
window._closeAllTips = function(except) {
  if (except !== 'cookieWarn') {
    const t = document.getElementById('cookieWarnTip');
    if (t) t.classList.remove('open');
  }
  if (except !== 'effTooltip') {
    const t = document.getElementById('effTooltip');
    if (t) t.classList.remove('visible');
  }
  if (except !== 'infoPanel') {
    const t = document.getElementById('infoPanel');
    if (t) t.classList.remove('open');
  }
};

// ── Cookie expiry warning tooltip ──
(function() {
  const warn = document.getElementById('cookieWarn');
  const tip  = document.getElementById('cookieWarnTip');
  if (!warn || !tip) return;
  warn.addEventListener('click', (e) => {
    e.stopPropagation();
    const opening = !tip.classList.contains('open');
    if (opening) window._closeAllTips('cookieWarn');
    tip.classList.toggle('open');
  });
  document.getElementById('app').addEventListener('click', (e) => {
    if (!tip.contains(e.target) && e.target !== warn) {
      tip.classList.remove('open');
    }
  });
})();

// ── Efficiency info tooltip ──
(function() {
  const btn = document.getElementById('effInfoBtn');
  const tip = document.getElementById('effTooltip');
  if (!btn || !tip) return;

  function positionTip() {
    const r    = btn.getBoundingClientRect();
    const tipW = 200, margin = 8;
    const tipH = tip.offsetHeight || 160;
    let left = r.left;
    if (left + tipW > window.innerWidth - margin) left = window.innerWidth - tipW - margin;
    tip.style.top  = (r.top - tipH - 7) + 'px';
    tip.style.left = left + 'px';
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const opening = !tip.classList.contains('visible');
    if (opening) { window._closeAllTips('effTooltip'); positionTip(); }
    tip.classList.toggle('visible');
  });

  document.getElementById('app').addEventListener('click', (e) => {
    if (!tip.contains(e.target) && !btn.contains(e.target)) {
      tip.classList.remove('visible');
    }
  });
})();

// ── Efficiency card expand / collapse ──
(function() {
  const body   = document.getElementById('effBody');
  const toggle = document.getElementById('effToggle');
  const hdr    = document.getElementById('effHdr');
  const app    = document.getElementById('app');
  if (!body || !toggle) return;

  let expanded = false;
  try { expanded = localStorage.getItem('effOpen') === '1'; } catch(e) {}

  function getAppH() { return parseInt(app.style.height) || H_BASE; }
  function sendResize(h) {
    app.style.height = h + 'px';
    window.webkit.messageHandlers.cm.postMessage({action:'resize', h});
  }

  // Measure full body height before any transitions
  body.style.maxHeight = 'none';
  const fullH = body.scrollHeight;

  // Apply initial state without animation
  body.style.maxHeight = expanded ? fullH + 'px' : '0px';
  body.classList.add(expanded ? 'open' : 'shut');
  if (!expanded) { body.style.paddingTop = '0'; }
  toggle.classList.toggle('open', expanded);

  function doToggle() {
    expanded = !expanded;
    try { localStorage.setItem('effOpen', expanded ? '1' : '0'); } catch(e) {}

    if (expanded) {
      body.style.paddingTop = '';
      body.style.maxHeight = fullH + 'px';
      body.classList.remove('shut'); body.classList.add('open');
    } else {
      body.style.maxHeight = '0px';
      body.classList.add('shut'); body.classList.remove('open');
    }
    toggle.classList.toggle('open', expanded);

    const currentH = getAppH();
    const newH = expanded ? currentH + fullH : currentH - fullH;
    setTimeout(() => sendResize(newH), 10);
  }

  // Full header row is clickable; exclude the info button
  if (hdr) {
    hdr.addEventListener('click', (e) => {
      if (e.target.closest('#effInfoBtn')) return;
      doToggle();
    });
  } else {
    toggle.addEventListener('click', doToggle);
  }

  // Expose reset so applyModules can collapse & sync closure state
  window._resetEff = function() {
    if (!expanded) return;
    expanded = false;
    try { localStorage.setItem('effOpen', '0'); } catch(e) {}
    body.style.transition = 'none';
    body.style.maxHeight  = '0px';
    body.classList.add('shut'); body.classList.remove('open');
    body.style.paddingTop = '0';
    toggle.classList.remove('open');
    void body.offsetHeight; // force synchronous reflow
    body.style.transition = '';
  };
})();

// ── 7-Day chart expand / collapse ──
(function() {
  const body   = document.getElementById('weekChartBody');
  const toggle = document.getElementById('weekChartToggle');
  const hdr    = document.getElementById('weekHdr');
  const app    = document.getElementById('app');
  if (!body || !toggle) return;

  // Default collapsed — H_BASE does NOT include the chart body height.
  // Only expand if the user explicitly opened it before.
  let expanded = false;
  try { expanded = localStorage.getItem('weekChartOpen') === '1'; } catch(e) {}
  window._weekExpanded = expanded;

  function getAppH() { return parseInt(app.style.height) || H_BASE; }
  function sendResize(h) {
    app.style.height = h + 'px';
    window.webkit.messageHandlers.cm.postMessage({action:'resize', h});
  }

  body.style.maxHeight = 'none';
  const fullH = body.scrollHeight;   // measured before any transitions

  body.style.maxHeight = expanded ? fullH + 'px' : '0px';
  body.classList.add(expanded ? 'open' : 'shut');
  if (!expanded) body.style.paddingTop = '0';
  toggle.classList.toggle('open', expanded);

  // If starting expanded (from saved pref), grow the popover to fit on first load
  if (expanded) {
    const initH = H_BASE + fullH;
    app.style.height = initH + 'px';
    setTimeout(() =>
      window.webkit.messageHandlers.cm.postMessage({action:'resize', h: initH}), 0);
  }

  function doToggle() {
    expanded = !expanded;
    window._weekExpanded = expanded;
    try { localStorage.setItem('weekChartOpen', expanded ? '1' : '0'); } catch(e) {}

    if (expanded) {
      body.style.paddingTop = '';
      body.style.maxHeight  = fullH + 'px';
      body.classList.remove('shut'); body.classList.add('open');
      window._weekChartReady = false;
      setTimeout(animateWeekBars, 60);
    } else {
      body.style.maxHeight = '0px';
      body.classList.add('shut'); body.classList.remove('open');
    }
    toggle.classList.toggle('open', expanded);

    const newH = getAppH() + (expanded ? fullH : -fullH);
    setTimeout(() => sendResize(newH), 10);
  }

  // Full header row is clickable
  if (hdr) {
    hdr.addEventListener('click', doToggle);
  } else {
    toggle.addEventListener('click', doToggle);
  }

  // Expose reset so applyModules can collapse & sync closure state
  window._resetWeek = function() {
    if (!expanded) return;
    expanded = false;
    window._weekExpanded = false;
    try { localStorage.setItem('weekChartOpen', '0'); } catch(e) {}
    body.style.transition = 'none';
    body.style.maxHeight  = '0px';
    body.classList.add('shut'); body.classList.remove('open');
    body.style.paddingTop = '0';
    toggle.classList.remove('open');
    void body.offsetHeight;
    body.style.transition = '';
  };
})();

// ── Project Breakdown expand / collapse ──
(function() {
  const body   = document.getElementById('projBody');
  const toggle = document.getElementById('projToggle');
  const hdr    = document.getElementById('projHdr');
  const app    = document.getElementById('app');
  if (!body || !toggle) return;

  let expanded = false;
  try { expanded = localStorage.getItem('projOpen') === '1'; } catch(e) {}

  // Body content is dynamic; don't pre-measure — measure at toggle time
  body.style.maxHeight = expanded ? '500px' : '0px';
  body.classList.add(expanded ? 'open' : 'shut');
  if (!expanded) body.style.paddingTop = '0';
  toggle.classList.toggle('open', expanded);

  function getAppH() { return parseInt(app.style.height) || H_BASE; }
  function sendResize(h) {
    app.style.height = h + 'px';
    window.webkit.messageHandlers.cm.postMessage({action:'resize', h});
  }

  function doToggle() {
    expanded = !expanded;
    try { localStorage.setItem('projOpen', expanded ? '1' : '0'); } catch(e) {}
    if (expanded) {
      body.style.paddingTop = '';
      // Measure actual content height
      const fullH = body.scrollHeight;
      body.style.maxHeight = fullH + 'px';
      body.classList.remove('shut'); body.classList.add('open');
      const sh = document.getElementById('projShimmer');
      if (sh) { sh.classList.remove('run'); void sh.offsetWidth; sh.classList.add('run'); }
      setTimeout(() => sendResize(getAppH() + fullH), 10);
    } else {
      const fullH = parseInt(body.style.maxHeight) || body.scrollHeight;
      body.style.maxHeight = '0px';
      body.classList.add('shut'); body.classList.remove('open');
      setTimeout(() => sendResize(getAppH() - fullH), 10);
    }
    toggle.classList.toggle('open', expanded);
  }

  if (hdr) hdr.addEventListener('click', doToggle);
  else     toggle.addEventListener('click', doToggle);

  // After rendering new bars, update maxHeight + popover height if currently expanded
  window._updateProjH = function() {
    if (!expanded) return;
    const oldH = parseInt(body.style.maxHeight) || 0;
    const newH = body.scrollHeight;
    if (newH === oldH) return;
    body.style.maxHeight = newH + 'px';
    sendResize(getAppH() + (newH - oldH));
  };

  // Collapse & sync closure for applyModules
  window._resetProject = function() {
    if (!expanded) return;
    const fullH = parseInt(body.style.maxHeight) || body.scrollHeight;
    expanded = false;
    try { localStorage.setItem('projOpen', '0'); } catch(e) {}
    body.style.transition = 'none';
    body.style.maxHeight  = '0px';
    body.classList.add('shut'); body.classList.remove('open');
    body.style.paddingTop = '0';
    toggle.classList.remove('open');
    void body.offsetHeight;
    body.style.transition = '';
  };

  // Tab switching: Session / Week
  ['projTabSess', 'projTabWeek'].forEach(id => {
    const tab = document.getElementById(id);
    if (!tab) return;
    tab.addEventListener('click', (e) => {
      e.stopPropagation();
      window._projMode = tab.dataset.mode;
      document.querySelectorAll('.proj-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      if (window._d) renderProjectBreakdown(window._d);
    });
  });
})();

// ── Hourly Heatmap expand / collapse ──
(function() {
  const body   = document.getElementById('heatmapBody');
  const toggle = document.getElementById('heatmapToggle');
  const hdr    = document.getElementById('heatmapHdr');
  const app    = document.getElementById('app');
  if (!body || !toggle) return;

  let expanded = false;
  try { expanded = localStorage.getItem('heatmapOpen') === '1'; } catch(e) {}

  // Heatmap grid renders dynamically; use a safe initial guess
  const HEATMAP_EST = 106; // 10 + 68 grid + 4 gap + 13 axis + 11 rounding
  body.style.maxHeight = expanded ? HEATMAP_EST + 'px' : '0px';
  body.classList.add(expanded ? 'open' : 'shut');
  if (!expanded) body.style.paddingTop = '0';
  toggle.classList.toggle('open', expanded);

  function getAppH() { return parseInt(app.style.height) || H_BASE; }
  function sendResize(h) {
    app.style.height = h + 'px';
    window.webkit.messageHandlers.cm.postMessage({action:'resize', h});
  }

  function doToggle() {
    expanded = !expanded;
    try { localStorage.setItem('heatmapOpen', expanded ? '1' : '0'); } catch(e) {}
    if (expanded) {
      body.style.paddingTop = '';
      const fullH = body.scrollHeight || HEATMAP_EST;
      body.style.maxHeight = fullH + 'px';
      body.classList.remove('shut'); body.classList.add('open');
      const sh = document.getElementById('heatmapShimmer');
      if (sh) { sh.classList.remove('run'); void sh.offsetWidth; sh.classList.add('run'); }
      setTimeout(() => sendResize(getAppH() + fullH), 10);
    } else {
      const fullH = parseInt(body.style.maxHeight) || HEATMAP_EST;
      body.style.maxHeight = '0px';
      body.classList.add('shut'); body.classList.remove('open');
      setTimeout(() => sendResize(getAppH() - fullH), 10);
    }
    toggle.classList.toggle('open', expanded);
  }

  if (hdr) hdr.addEventListener('click', doToggle);
  else     toggle.addEventListener('click', doToggle);

  // Snap maxHeight to actual rendered content after first render
  window._updateHeatmapH = function() {
    if (!expanded) return;
    const oldH = parseInt(body.style.maxHeight) || 0;
    const newH = body.scrollHeight || HEATMAP_EST;
    if (newH === oldH) return;
    body.style.maxHeight = newH + 'px';
    sendResize(getAppH() + (newH - oldH));
  };

  window._resetHeatmap = function() {
    if (!expanded) return;
    expanded = false;
    try { localStorage.setItem('heatmapOpen', '0'); } catch(e) {}
    body.style.transition = 'none';
    body.style.maxHeight  = '0px';
    body.classList.add('shut'); body.classList.remove('open');
    body.style.paddingTop = '0';
    toggle.classList.remove('open');
    void body.offsetHeight;
    body.style.transition = '';
  };
})();

// ── Leverage sparkline expand / collapse ──
(function() {
  const body   = document.getElementById('levSparkBody');
  const toggle = document.getElementById('levToggle');
  const hdr    = document.getElementById('levHdr');
  const app    = document.getElementById('app');
  if (!body || !toggle) return;

  let expanded = false;
  try { expanded = localStorage.getItem('levSparkOpen') === '1'; } catch(e) {}

  function getAppH() { return parseInt(app.style.height) || H_BASE; }
  function sendResize(h) {
    app.style.height = h + 'px';
    window.webkit.messageHandlers.cm.postMessage({action:'resize', h});
  }

  // SVG is empty at page-load time, so scrollHeight may under-count.
  // Use a fixed known height: 10px padding + 52px SVG + 5px gap + 18px labels = 85px
  body.style.maxHeight = 'none';
  const fullH = Math.max(85, body.scrollHeight);

  body.style.maxHeight = expanded ? fullH + 'px' : '0px';
  body.classList.add(expanded ? 'open' : 'shut');
  if (!expanded) body.style.paddingTop = '0';
  toggle.classList.toggle('open', expanded);

  if (expanded) {
    const initH = H_BASE + fullH;
    app.style.height = initH + 'px';
    setTimeout(() =>
      window.webkit.messageHandlers.cm.postMessage({action:'resize', h: initH}), 0);
  }

  function doToggle() {
    expanded = !expanded;
    try { localStorage.setItem('levSparkOpen', expanded ? '1' : '0'); } catch(e) {}

    if (expanded) {
      body.style.paddingTop = '';
      body.style.maxHeight  = fullH + 'px';
      body.classList.remove('shut'); body.classList.add('open');
      const sh = document.getElementById('levShimmer');
      if (sh) { sh.classList.remove('run'); void sh.offsetWidth; sh.classList.add('run'); }
    } else {
      body.style.maxHeight = '0px';
      body.classList.add('shut'); body.classList.remove('open');
    }
    toggle.classList.toggle('open', expanded);

    const newH = getAppH() + (expanded ? fullH : -fullH);
    setTimeout(() => sendResize(newH), 10);
  }

  if (hdr) {
    hdr.addEventListener('click', doToggle);
  } else {
    toggle.addEventListener('click', doToggle);
  }
})();

// ── Info panel ──
(function() {
  const btn   = document.getElementById('infoToggle');
  const panel = document.getElementById('infoPanel');
  const close = document.getElementById('infoClose');

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const opening = !panel.classList.contains('open');
    if (opening) window._closeAllTips('infoPanel');
    panel.classList.toggle('open');
  });
  close.addEventListener('click', () => panel.classList.remove('open'));
  document.getElementById('app').addEventListener('click', (e) => {
    if (!panel.contains(e.target) && e.target !== btn) {
      panel.classList.remove('open');
    }
  });
})();
</script>
</body>
</html>"""

# ─── Script message handler (JS → Python) ─────────────────────────────────────

class MsgHandler(NSObject):
    delegate = None

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        try:
            body   = dict(message.body())
            action = body.get("action")
            if action == "refresh" and self.delegate:
                iv = int(body.get("iv", 60))
                self.delegate.setInterval_(iv)
            elif action == "theme" and self.delegate:
                theme = str(body.get("value", "system"))
                self.delegate.setTheme_(theme)
            elif action == "cookie" and self.delegate:
                value = str(body.get("value", "")).strip()
                self.delegate.saveCookie_(value)
            elif action == "onboarding_done" and self.delegate:
                self.delegate.settings["onboarding_done"] = True
                save_settings(self.delegate.settings)
            elif action == "resize" and self.delegate:
                h = str(int(body.get("h", POPOVER_H)))
                self.delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                    objc.selector(self.delegate.resizeH_, signature=b"v@:@"), h, True
                )
        except Exception as e:
            print("MsgHandler error:", e)

MsgHandler.delegate = None

# ─── Popover view controller ──────────────────────────────────────────────────

class PopoverVC(NSViewController):
    webView     = None
    vfxView     = None
    _msgHandler = None

    def init(self):
        self = objc.super(PopoverVC, self).initWithNibName_bundle_(None, None)
        if self is None:
            return None
        self._buildView()
        return self

    @objc.python_method
    def _buildView(self):
        w      = POPOVER_W
        h_base = POPOVER_H
        h_full = POPOVER_H_COOKIE
        wv_y   = -(h_full - h_base)

        container_frame = NSMakeRect(0, 0, w, h_base)
        container = NSView.alloc().initWithFrame_(container_frame)

        vfx = NSVisualEffectView.alloc().initWithFrame_(container_frame)
        vfx.setMaterial_(NSVisualEffectMaterialPopover)
        vfx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        vfx.setState_(NSVisualEffectStateActive)
        container.addSubview_(vfx)

        handler = MsgHandler.alloc().init()
        self._msgHandler = handler

        config = WKWebViewConfiguration.alloc().init()
        ucc    = WKUserContentController.alloc().init()
        ucc.addScriptMessageHandler_name_(handler, "cm")
        config.setUserContentController_(ucc)

        wv_frame = NSMakeRect(0, wv_y, w, h_full)
        wv = WKWebView.alloc().initWithFrame_configuration_(wv_frame, config)
        wv.setValue_forKey_(False, "drawsBackground")
        wv.setBackgroundColor_(NSColor.clearColor())
        wv.loadHTMLString_baseURL_(HTML, None)
        container.addSubview_(wv)

        self.webView = wv
        self.vfxView = vfx
        self.setView_(container)

# ─── App delegate ─────────────────────────────────────────────────────────────

class AppDelegate(NSObject):

    def applicationDidFinishLaunching_(self, _note):
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
        self.settings     = load_settings()
        self._last_update = None
        self._timer       = None
        self._payload     = None

        bar = NSStatusBar.systemStatusBar()
        self.item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        btn = self.item.button()
        btn.setTarget_(self)
        btn.setAction_(objc.selector(self.togglePopover_, signature=b"v@:@"))
        # Receive both left and right mouse-up events in the action
        btn.sendActionOn_(4 | 16)   # NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp
        self._setTitle("--%")

        # ── Pre-bake horizontally-flipped powermeter bitmaps (colour baked in) ──
        self._flipped_orange = None
        self._flipped_red    = None

        # Pre-bake flipped powermeter bitmaps with colour baked in via DestinationIn compositing
        self._flipped_orange = None
        self._flipped_red    = None

        def _bake_flipped(color):
            try:
                from AppKit import (NSImageSymbolConfiguration, NSGraphicsContext,
                                    NSAffineTransform, NSBitmapImageRep, NSRectFill)
                src = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                    "powermeter", None)
                if src is None:
                    return None
                cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(
                    18.0, 0.4, 2)
                src = src.imageWithSymbolConfiguration_(cfg)
                src.setSize_(NSMakeSize(18, 18))

                # 36×36 pixel bitmap (≈@2x for 18pt)
                rep = NSBitmapImageRep.alloc() \
                    .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                        None, 36, 36, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
                gctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
                NSGraphicsContext.saveGraphicsState()
                NSGraphicsContext.setCurrentContext_(gctx)

                # Step 1: fill entire bitmap with target colour (NSRectFill respects color.set())
                color.set()
                NSRectFill(NSMakeRect(0, 0, 36, 36))

                # Step 2: draw flipped symbol using DestinationIn (7) — masks colour to symbol alpha
                xf = NSAffineTransform.transform()
                xf.translateXBy_yBy_(36.0, 0.0)   # full pixel width
                xf.scaleXBy_yBy_(-1.0, 1.0)
                xf.concat()
                src.drawInRect_fromRect_operation_fraction_(
                    NSMakeRect(0, 0, 36, 36),       # full pixel rect
                    NSMakeRect(0, 0, 0, 0),
                    7,   # NSCompositingOperationDestinationIn — keep dst where src has alpha
                    1.0
                )
                NSGraphicsContext.restoreGraphicsState()

                out = NSImage.alloc().initWithSize_(NSMakeSize(18, 18))
                out.addRepresentation_(rep)
                out.setTemplate_(False)  # colour is already baked in
                return out
            except Exception as e:
                print(f"[meter] _bake_flipped failed: {e}", flush=True)
                return None

        self._flipped_orange = _bake_flipped(NSColor.systemOrangeColor())
        self._flipped_red    = _bake_flipped(
            NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.396, 0.302, 1.0))
        print(f"[meter] flipped bitmaps: orange={bool(self._flipped_orange)} red={bool(self._flipped_red)}", flush=True)

        self._vc = PopoverVC.alloc().init()
        self._vc._msgHandler.delegate = self

        self._popover = NSPopover.alloc().init()
        self._popover.setContentSize_(NSMakeSize(POPOVER_W, POPOVER_H))
        self._popover.setBehavior_(1)
        self._popover.setAnimates_(True)
        self._popover.setContentViewController_(self._vc)

        self._doRefresh()
        self._scheduleTimer()

    @objc.python_method
    def _scheduleTimer(self):
        if self._timer:
            self._timer.invalidate()
            self._timer = None
        iv = self.settings.get("interval", 300)
        if iv > 0:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                float(iv), self,
                objc.selector(self.timerTick_, signature=b"v@:@"),
                None, True
            )

    def timerTick_(self, _timer):
        self._doRefresh()

    @objc.python_method
    def _doRefresh(self):
        import threading
        def worker():
            payload = build_payload(self.settings)
            self._payload = payload
            self._last_update = datetime.now()
            self._applyPayload(payload)
        threading.Thread(target=worker, daemon=True).start()

    @objc.python_method
    def _buildModulesDict(self):
        return {
            "eff":     self.settings.get("module_eff",     True),
            "week":    self.settings.get("module_week",    True),
            "project": self.settings.get("module_project", False),
            "heatmap": self.settings.get("module_heatmap", False),
        }

    @objc.python_method
    def _applyPayload(self, p):
        # Leverage ratio from meter_core feeds the efficiency display
        eff_rate = p.get("lev_ratio")          # output / input ratio (float | None)
        eff_min  = p.get("lev_count", 0)       # message count in session
        p = dict(p, eff_rate=eff_rate, eff_min=eff_min,
                 modules=self._buildModulesDict())
        self._payload = p
        pct   = p["session_pct"]
        title = f"{int(pct)}%"
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            objc.selector(self.setTitleStr_, signature=b"v@:@"),
            title, False
        )
        if self._popover.isShown():
            self._injectData(p)

    def setTitleStr_(self, s):
        self._setTitle(s)

    @objc.python_method
    def _setTitle(self, s):
        btn  = self.item.button()
        # Text: regular weight, label color (never changes)
        font = NSFont.monospacedSystemFontOfSize_weight_(13.0, 0.0)

        # Icon color only — based on session utilisation
        try:
            pct_val = float(s.replace('%', '').strip())
        except ValueError:
            pct_val = -1

        if pct_val >= 90:
            # Carbon danger red — #FF654D
            icon_color = NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.396, 0.302, 1.0)
        elif pct_val >= 75:
            icon_color = NSColor.systemOrangeColor()
        else:
            icon_color = NSColor.labelColor()

        from AppKit import NSTextAttachment, NSMutableAttributedString

        # Pick image: pre-baked colour bitmap at ≥75%, live SF Symbol otherwise
        _fo = getattr(self, '_flipped_orange', None)
        _fr = getattr(self, '_flipped_red',    None)
        if pct_val >= 90 and _fr:
            img, use_flipped = _fr, True
        elif pct_val >= 75 and _fo:
            img, use_flipped = _fo, True
        else:
            img         = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                              "powermeter", None)
            use_flipped = False

        result = NSMutableAttributedString.alloc().init()

        if img is not None:
            if not use_flipped:
                # Live SF Symbol — apply bold config and template
                try:
                    from AppKit import NSImageSymbolConfiguration
                    cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(
                        18.0, 0.4, 2)
                    img = img.imageWithSymbolConfiguration_(cfg)
                except Exception:
                    pass
                img = img.copy()
                img.setSize_(NSMakeSize(18, 18))
                img.setTemplate_(True)

            att = NSTextAttachment.alloc().init()
            att.setImage_(img)
            att.setBounds_(NSMakeRect(0, -3.5, 18, 18))
            icon_as = NSMutableAttributedString.alloc().initWithAttributedString_(
                NSAttributedString.attributedStringWithAttachment_(att)
            )
            if not use_flipped:
                # Live SF Symbol template — apply foreground colour attribute
                icon_as.addAttribute_value_range_(
                    NSForegroundColorAttributeName, icon_color,
                    NSMakeRange(0, icon_as.length())
                )
            result.appendAttributedString_(icon_as)
            # Thin gap — one narrow space
            result.appendAttributedString_(
                NSAttributedString.alloc().initWithString_(" ")
            )

        # Text in label color, regular weight
        attrs = {
            NSFontAttributeName:            font,
            NSForegroundColorAttributeName: NSColor.labelColor(),
        }
        result.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(s, attrs)
        )
        btn.setAttributedTitle_(result)

    @objc.python_method
    def _injectData(self, p, animate=False):
        if self._last_update:
            delta = (datetime.now() - self._last_update).seconds
            p = dict(p, updated="just now" if delta < 60 else f"{delta//60}m ago")
        if animate:
            p = dict(p, _animate=True)
        js = f"window.updateData({json.dumps(p)})"
        self._vc.webView.evaluateJavaScript_completionHandler_(js, None)

    def togglePopover_(self, sender):
        # Detect right-click (NSEventTypeRightMouseUp = 4)
        event = NSApplication.sharedApplication().currentEvent()
        if event is not None and event.type() == 4:
            self._showContextMenu()
            return

        if self._popover.isShown():
            self._popover.performClose_(sender)
        else:
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            btn = self.item.button()
            self._popover.showRelativeToRect_ofView_preferredEdge_(
                btn.bounds(), btn, NSRectEdgeMinY
            )
            if self._payload:
                self._injectData(self._payload, animate=True)
            else:
                self._doRefresh()

    @objc.python_method
    def _showContextMenu(self):
        # Tag map:  10=Dark  11=Light
        #           20=Eff  21=Week  22=Project  23=Heatmap
        #           99=Quit
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        act  = objc.selector(self.menuAction_, signature=b"v@:@")

        # ── Appearance submenu ────────────────────────────────────────────────
        appearItem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Appearance", None, "")
        appearMenu = NSMenu.alloc().initWithTitle_("Appearance")
        appearMenu.setAutoenablesItems_(False)
        cur_theme  = self.settings.get("theme", "dark")
        for label, tag, active in [
            ("Dark Mode",  10, cur_theme == "dark"),
            ("Light Mode", 11, cur_theme == "light"),
        ]:
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, act, "")
            it.setTarget_(self); it.setEnabled_(True)
            it.setTag_(tag); it.setState_(1 if active else 0)
            appearMenu.addItem_(it)
        appearItem.setSubmenu_(appearMenu)
        menu.addItem_(appearItem)

        # ── Modules submenu ───────────────────────────────────────────────────
        modulesItem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Modules", None, "")
        modulesMenu = NSMenu.alloc().initWithTitle_("Modules")
        modulesMenu.setAutoenablesItems_(False)
        for label, tag, key, default in [
            ("Prompt Efficiency", 20, "module_eff",  True),
            ("7-Day Usage",       21, "module_week", True),
        ]:
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, act, "")
            it.setTarget_(self); it.setEnabled_(True)
            it.setTag_(tag); it.setState_(1 if self.settings.get(key, default) else 0)
            modulesMenu.addItem_(it)
        modulesMenu.addItem_(NSMenuItem.separatorItem())
        for label, tag, key, default in [
            ("Project Breakdown", 22, "module_project", False),
            ("Hourly Heatmap",    23, "module_heatmap", False),
        ]:
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, act, "")
            it.setTarget_(self); it.setEnabled_(True)
            it.setTag_(tag); it.setState_(1 if self.settings.get(key, default) else 0)
            modulesMenu.addItem_(it)
        modulesItem.setSubmenu_(modulesMenu)
        menu.addItem_(modulesItem)

        menu.addItem_(NSMenuItem.separatorItem())

        it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit", act, "")
        it.setTarget_(self); it.setEnabled_(True); it.setTag_(99)
        menu.addItem_(it)

        event = NSApplication.sharedApplication().currentEvent()
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self.item.button())

    # Single ObjC-visible action routed by tag — avoids all selector-name issues
    def menuAction_(self, sender):
        tag = int(sender.tag())
        if tag == 99:
            NSApplication.sharedApplication().terminate_(None)
            return
        if tag in (10, 11):
            theme = "dark" if tag == 10 else "light"
            self.settings["theme"] = theme
            save_settings(self.settings)
            # Apply immediately whether or not the popover is open
            if self._payload:
                self._payload = dict(self._payload, theme=theme)
                if self._popover.isShown():
                    self._injectData(self._payload)
            return
        # Module toggles
        _MODULE_MAP = {
            20: ("module_eff",     True),
            21: ("module_week",    True),
            22: ("module_project", False),
            23: ("module_heatmap", False),
        }
        if tag in _MODULE_MAP:
            key, default = _MODULE_MAP[tag]
            new_val = not self.settings.get(key, default)
            self.settings[key] = new_val
            save_settings(self.settings)
            if self._payload:
                self._payload = dict(self._payload, modules=self._buildModulesDict())
                if self._popover.isShown():
                    self._injectData(self._payload)
            if new_val and key in ("module_project", "module_heatmap"):
                self._doRefresh()

    def _menuQuit_(self, sender):
        NSApplication.sharedApplication().terminate_(None)


    def setInterval_(self, iv):
        self.settings["interval"] = int(iv)
        save_settings(self.settings)
        self._scheduleTimer()
        self._doRefresh()

    def setTheme_(self, theme):
        self.settings["theme"] = str(theme)
        save_settings(self.settings)
        if self._payload and self._popover.isShown():
            self._injectData(dict(self._payload, theme=str(theme)))

    def saveCookie_(self, value):
        """Save session cookie from JS, verify it works, notify the webview."""
        import threading
        def worker():
            # Quick validation — temporarily apply the key and try fetching
            test_settings = dict(self.settings, session_key=value)
            test_settings.pop("org_id", None)   # force fresh org lookup
            test = fetch_claude_ai_usage(test_settings)
            ok   = test is not None
            if ok:
                self.settings["session_key"] = value
                self.settings["org_id"]      = test_settings.get("org_id", "")
                save_settings(self.settings)
            # Report back to JS on main thread
            js = f"window.cookieSaved({'true' if ok else 'false'})"
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                objc.selector(self.evalJS_, signature=b"v@:@"), js, False
            )
            if ok:
                self._doRefresh()
        threading.Thread(target=worker, daemon=True).start()

    def evalJS_(self, js):
        if self._vc and self._vc.webView:
            self._vc.webView.evaluateJavaScript_completionHandler_(js, None)

    def resizeH_(self, h_str):
        h   = float(str(h_str))
        w   = float(POPOVER_W)
        h_f = float(POPOVER_H_COOKIE)
        wv_y = -(h_f - h)
        self._popover.setContentSize_(NSMakeSize(w, h))
        self._vc.webView.setFrame_(NSMakeRect(0, wv_y, w, h_f))
        self._vc.vfxView.setFrame_(NSMakeRect(0, 0, w, h))

# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app      = NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()
