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
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

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

SETTINGS_FILE = os.path.expanduser("~/.claude_meter_settings.json")
PROJECTS_GLOB = os.path.expanduser("~/.claude/projects/**/*.jsonl")
DEFAULT_LIMIT    = 45_000_000    # Claude Pro monthly (approximate)
SESSION_LIMIT    = 12_830_000    # Claude Pro 5-hour session limit (calibrated)
WEEK_LIMIT       = 179_000_000   # Claude Pro weekly limit (calibrated)
PT_OFFSET        = timedelta(hours=7)   # UTC-7 (PDT); use 8 in winter (PST)

POPOVER_W  = 300
POPOVER_H  = 653   # base: trimmed footer (-12px)
POPOVER_H_COOKIE = 868  # WKWebView ceiling: 653 + 113 (7-day body) + 70 (cookie) + buffer

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
  width: 300px; height: 653px;
  background: linear-gradient(175deg, var(--g1) 0%, var(--g2) 50%, var(--g3) 100%);
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.12);
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
  background: rgba(10,22,42,0.96);
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
  border-top-color: rgba(10,22,42,0.96);
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
  background: rgba(25,27,31,0.97);
  border-color: rgba(255,255,255,0.10);
}
html[data-vivid="on"] .eff-tooltip::after {
  border-top-color: rgba(25,27,31,0.97);
}
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(1) .tip-dot { background: #AAD7FE !important; }
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(2) .tip-dot { background: #98CECC !important; }
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(3) .tip-dot { background: #ECB967 !important; }
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(4) .tip-dot { background: #F09060 !important; }
html[data-vivid="on"] .eff-tooltip .tip-row:nth-child(5) .tip-dot { background: #FF654D !important; }
/* Carbon — cookie expired tooltip + info panel match eff-tooltip background */
html[data-vivid="on"] .cookie-warn-tip {
  background: rgba(25,27,31,0.97);
  border-color: rgba(255,160,50,0.4);
}
html[data-vivid="on"] .cookie-warn-tip::before {
  border-top-color: rgba(25,27,31,0.97);
}
html[data-vivid="on"] .info-panel {
  background: rgba(25,27,31,0.97);
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
  background: rgba(245,246,248,0.98);
  border-color: rgba(0,0,0,0.12);
}
html[data-vivid="on"][data-theme="light"] .eff-tooltip::after {
  border-top-color: rgba(245,246,248,0.98);
}
html[data-vivid="on"][data-theme="light"] .cookie-warn-tip {
  background: rgba(245,246,248,0.98);
  border-color: rgba(220,140,30,0.55);
}
html[data-vivid="on"][data-theme="light"] .cookie-warn-tip::before {
  border-top-color: rgba(245,246,248,0.98);
}
html[data-vivid="on"][data-theme="light"] .info-panel {
  background: rgba(245,246,248,0.98);
  border-color: rgba(0,0,0,0.12);
}
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
  background: rgba(20,10,0,0.97);
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
  border-top-color: rgba(20,10,0,0.97);
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
  background: rgba(10,22,42,0.96);
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

  <!-- Expired cookie banner — visible only when data-expired="on" -->
  <div class="expired-banner" id="expiredBanner">
    <span class="expired-banner-text"><b>Session expired</b> — data shown may be outdated</span>
    <button class="expired-update-btn" id="expiredUpdateBtn">Update Key →</button>
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
    <div id="effBody">
      <div class="eff-segs">
        <div class="eff-seg" id="es0" style="background:#52BAFF"></div>
        <div class="eff-seg" id="es1" style="background:#8882F0"></div>
        <div class="eff-seg" id="es2" style="background:#A87CF5"></div>
        <div class="eff-seg" id="es3" style="background:#D46090"></div>
        <div class="eff-seg" id="es4" style="background:#F26280"></div>
      </div>
      <div class="eff-lbls">
        <span class="eff-lbl" id="el0">Sharp</span>
        <span class="eff-lbl" id="el1">Focused</span>
        <span class="eff-lbl" id="el2">Moderate</span>
        <span class="eff-lbl" id="el3">Verbose</span>
        <span class="eff-lbl" id="el4">Scattered</span>
      </div>
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
      Measures your average token burn rate in <b>tokens per minute</b> across recent prompts.
      <div class="tip-row"><span class="tip-dot" style="background:#52BAFF"></span><span><b>Sharp</b> ≤150 — concise, targeted prompts</span></div>
      <div class="tip-row"><span class="tip-dot" style="background:#8882F0"></span><span><b>Focused</b> 151–350 — clear with context</span></div>
      <div class="tip-row"><span class="tip-dot" style="background:#A87CF5"></span><span><b>Moderate</b> 351–600 — some verbosity</span></div>
      <div class="tip-row"><span class="tip-dot" style="background:#D46090"></span><span><b>Verbose</b> 601–900 — heavy prompts</span></div>
      <div class="tip-row"><span class="tip-dot" style="background:#F26280"></span><span><b>Scattered</b> >900 — very high token rate</span></div>
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

  <!-- Footer -->
  <div class="footer">
    <div class="footer-row">
      <div style="display:flex;align-items:center;gap:5px;">
        <span class="footer-lbl">Session Cookie</span>
        <div class="cookie-warn-wrap">
          <span class="cookie-warn" id="cookieWarn">⚠</span>
          <div class="cookie-warn-tip" id="cookieWarnTip">
            <div class="cookie-warn-title">Session cookie expired</div>
            <div class="cookie-warn-body">Your session key is no longer valid. Click <b>Update Cookie Key</b> to paste a fresh one from Chrome.</div>
          </div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:5px;">
        <button class="info-icon-btn" id="infoToggle" title="How to find your sessionKey">ⓘ</button>
        <button class="cookie-btn" id="cookieToggle">Update Cookie Key</button>
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
      <span class="info-title">Finding your sessionKey</span>
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
  // Hide all arcs instantly, then fade each in via inline transition
  arcs.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.classList.remove('arc-pulse-low','arc-pulse-med','arc-pulse-high');
      el.style.transition = 'none';
      el.style.opacity = '0';
    }
  });
  const delays = [0, 280, 560];
  arcs.forEach((id, i) => {
    setTimeout(() => {
      const el = document.getElementById(id);
      if (el) {
        el.style.transition = 'opacity 0.85s ease';
        el.style.opacity = '1';
      }
      // After last arc finishes fading in, start the pulse (clears inline styles)
      if (i === arcs.length - 1 && callback) setTimeout(callback, 900);
    }, delays[i]);
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
function setStatusAnimated(text) {
  const el = document.getElementById('heroStatus');
  if (!el || text === _lastStatus) return;
  _lastStatus = text;
  el.style.transition = 'opacity 0.18s ease';
  el.style.opacity = '0';
  setTimeout(() => { el.textContent = text; el.style.opacity = '1'; }, 185);
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
  if (el._countUpRaf) cancelAnimationFrame(el._countUpRaf);
  const start     = parseFloat(el.textContent) || 0;
  const startTime = performance.now();
  function step(now) {
    const t = Math.min((now - startTime) / duration, 1);
    const ease = 1 - Math.pow(1 - t, 3);
    el.textContent = Math.round(start + (target - start) * ease);
    if (el.id === 'heroPctNum') _repositionPctSign();
    if (t < 1) {
      el._countUpRaf = requestAnimationFrame(step);
    } else {
      el._countUpRaf = null;
      popEl(el);
    }
  }
  el._countUpRaf = requestAnimationFrame(step);
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
      // Default: reset gradient arc then animate it in
      if (fillEl) {
        fillEl.classList.remove('arc-glow');
        fillEl.style.transition = 'none';
        fillEl.style.strokeDasharray = '0 ' + (GAUGE_LEN + 10).toFixed(2);
        void window.getComputedStyle(fillEl).strokeDasharray;
        fillEl.style.transition = '';
      }
      requestAnimationFrame(() => requestAnimationFrame(() => {
        updateArc(pct);
        fillEl?.classList.toggle('arc-glow', pct > 0);
      }));
    }
  } else {
    updateArc(pct);
    if (!isCarbon()) fillEl?.classList.toggle('arc-glow', pct > 0);
  }

  const heroPctEl = document.getElementById('heroPctNum');
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

  renderEfficiency(d);
  if (d.daily_7) renderWeekChart(d.daily_7);
}

// ── Prompt Efficiency ─────────────────────────────────────────────────────────
const EFF_ZONES  = ['Sharp','Focused','Moderate','Verbose','Scattered'];
const EFF_COLORS = ['#52BAFF','#8882F0','#A87CF5','#D46090','#F26280'];
const EFF_BOUNDS = [150, 350, 600, 900, Infinity];

function effZone(rate) {
  for (let i = 0; i < EFF_BOUNDS.length; i++)
    if (rate <= EFF_BOUNDS[i]) return i;
  return 4;
}

function renderEfficiency(d) {
  const rate = (d.eff_rate !== null && d.eff_rate !== undefined) ? d.eff_rate : null;
  const elMin = d.eff_min || 0;

  const today = new Date().toISOString().slice(0, 10);
  let hist = {};
  try { hist = JSON.parse(localStorage.getItem('effHist') || '{}'); } catch(e) {}

  if (rate !== null) {
    const prev = hist[today] || {avg: rate, n: 0};
    const n = prev.n + 1;
    hist[today] = {avg: (prev.avg * prev.n + rate) / n, n};
    try { localStorage.setItem('effHist', JSON.stringify(hist)); } catch(e) {}
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
      const minLabel = elMin < 2 ? '<2 min' : elMin + ' min';
      metaEl.textContent = '~' + rate + ' tok/min  ·  ' + minLabel + ' of data';
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

// ── 7-Day usage chart (segmented LED-style bars) ─────────────────────────────
window._weekExpanded  = true;   // kept in sync by toggle IIFE
window._weekChartReady = false; // animate once per open

const N_SEGS = 10;
// Segment colours: index 0 = bottom = red, index 9 = top = teal
const SEG_COLORS = [
  '#FF6B6B','#FF7C5C','#FF9045','#FFA830',
  '#FFC857','#D4C96A','#7ECBA4','#45C4B8',
  '#36C5C5','#4ECDC4'
];

function animateWeekBars() {
  const cols = document.querySelectorAll('#weekChart .week-bar-col');
  cols.forEach((col, ci) => {
    // .won segs are in DOM order top→bottom; reverse to animate bottom→top
    const active = [...col.querySelectorAll('.wseg.won')].reverse();
    active.forEach((seg, si) => {
      seg.style.opacity = '0';
      setTimeout(() => { seg.style.opacity = '1'; }, ci * 48 + si * 20);
    });
  });
}

function renderWeekChart(daily7) {
  if (!daily7 || !daily7.length) return;
  const chart    = document.getElementById('weekChart');
  const labelsEl = document.getElementById('weekLabels');
  if (!chart || !labelsEl) return;

  const max     = Math.max(...daily7.map(d => d.tokens), 1);
  chart.innerHTML = ''; labelsEl.innerHTML = '';

  daily7.forEach((d, ci) => {
    const isToday  = !!d.today;
    const active   = d.tokens > 0 ? Math.max(1, Math.round(d.tokens / max * N_SEGS)) : 0;
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



// ── Shared height constants (used by cookie form + efficiency toggle) ──
const H_BASE   = 653;   // base: trimmed footer (-12px)
const H_COOKIE = 723;   // H_BASE + 70 for cookie form

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

  function openForm() {
    form.classList.add('open');
    toggle.classList.remove('active');   // button returns to resting state when form opens
    input.classList.remove('cookie-typed');  // reset typed state so amber border shows
    app.style.height = H_COOKIE + 'px';
    window.webkit.messageHandlers.cm.postMessage({action:'resize', h: H_COOKIE});
    setTimeout(() => input.focus(), 240);
  }

  function closeForm() {
    form.classList.remove('open');
    toggle.classList.remove('active');
    input.classList.remove('cookie-typed');
    app.style.height = H_BASE + 'px';
    window.webkit.messageHandlers.cm.postMessage({action:'resize', h: H_BASE});
    input.value = ''; status.textContent = ''; status.className = 'cookie-status';
  }

  // Amber border clears only once the user actually starts typing
  input.addEventListener('input', () => input.classList.add('cookie-typed'));

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

// ── Cookie expiry warning tooltip ──
(function() {
  const warn = document.getElementById('cookieWarn');
  const tip  = document.getElementById('cookieWarnTip');
  if (!warn || !tip) return;
  warn.addEventListener('click', (e) => {
    e.stopPropagation();
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
    // tooltip is position:fixed opacity:0 (not display:none) so offsetHeight is valid
    const tipH = tip.offsetHeight || 160;
    let left = r.left;
    if (left + tipW > window.innerWidth - margin) left = window.innerWidth - tipW - margin;
    tip.style.top  = (r.top - tipH - 7) + 'px';
    tip.style.left = left + 'px';
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const opening = !tip.classList.contains('visible');
    if (opening) positionTip();
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

  let expanded = true;
  try { expanded = localStorage.getItem('effOpen') !== '0'; } catch(e) {}

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
})();

// ── Info panel ──
(function() {
  const btn   = document.getElementById('infoToggle');
  const panel = document.getElementById('infoPanel');
  const close = document.getElementById('infoClose');

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
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

# ─── Data ─────────────────────────────────────────────────────────────────────

import urllib.request
import urllib.error

def fetch_claude_ai_usage(settings):
    """Fetch real usage from claude.ai /usage API.
    Returns dict with session_pct, session_reset, session_reset_epoch,
    week_pct, week_reset, week_reset_epoch — or None on failure."""
    session_key = settings.get("session_key", "")
    if not session_key:
        return None

    headers = {
        "Cookie":     f"sessionKey={session_key}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept":     "application/json",
        "Referer":    "https://claude.ai/settings/usage",
    }

    # Cache org_id in settings to avoid extra round-trip each refresh
    org_id = settings.get("org_id")
    if not org_id:
        try:
            req = urllib.request.Request("https://claude.ai/api/organizations", headers=headers)
            with urllib.request.urlopen(req, timeout=8) as r:
                orgs = json.loads(r.read())
            org_id = orgs[0].get("uuid") or orgs[0].get("id")
            settings["org_id"] = org_id
            save_settings(settings)
        except Exception as e:
            print("fetch orgs error:", e)
            return None

    try:
        url = f"https://claude.ai/api/organizations/{org_id}/usage"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
    except Exception as e:
        print("fetch usage error:", e)
        return None

    fh = data.get("five_hour") or {}
    sd = data.get("seven_day")  or {}

    def parse_reset(iso):
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt, int(dt.timestamp() * 1000)
        except Exception:
            return None, None

    sess_dt, sess_epoch = parse_reset(fh.get("resets_at", ""))
    week_dt, week_epoch = parse_reset(sd.get("resets_at", ""))

    return {
        "session_pct":         round(float(fh.get("utilization") or 0), 1),
        "session_reset":       _fmt_pt_time(sess_dt) if sess_dt else "--",
        "session_reset_epoch": sess_epoch or 0,
        "week_pct":            round(float(sd.get("utilization")  or 0), 1),
        "week_reset":          _fmt_pt_date(week_dt) if week_dt else "--",
        "week_reset_epoch":    week_epoch or 0,
        "source":              "live",
    }

def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"limit": DEFAULT_LIMIT, "interval": 300, "theme": "dark"}

def save_settings(s):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f)

def detect_session_start():
    """Find when the current Anthropic 5h session started.
    Scans recent JSONL timestamps and returns the first message
    after the most recent idle gap >= 30 minutes.
    Falls back to now - 5h if no gap is found."""
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=8)
    timestamps = []
    for path in glob.glob(PROJECTS_GLOB, recursive=True):
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    msg = d.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get("usage")
                    if not usage:
                        continue
                    if not any(usage.get(k, 0) for k in [
                        "input_tokens", "cache_creation_input_tokens",
                        "cache_read_input_tokens", "output_tokens"
                    ]):
                        continue
                    ts_str = d.get("timestamp", "")
                    if not ts_str:
                        continue
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if ts >= cutoff:
                        timestamps.append(ts)
        except Exception:
            continue

    if not timestamps:
        return now - timedelta(hours=5)

    timestamps = sorted(set(timestamps))
    session_start = timestamps[0]
    for i in range(1, len(timestamps)):
        gap_min = (timestamps[i] - timestamps[i-1]).total_seconds() / 60
        if gap_min >= 30:
            session_start = timestamps[i]
    return session_start

def _week_start_utc(now_utc):
    """Return the UTC datetime for the start of the current weekly billing period.
    Anthropic's weekly window resets on the same weekday as the user's plan start.
    We detect it as: the most recent occurrence of today's weekday-at-midnight-PT,
    working backwards to find the Saturday (or whatever day) that opened this window.
    Since the user's week resets on Saturday 12am PT we use weekday=5 (Saturday).
    """
    # Convert now to PT (approximate — ignores DST edge cases)
    now_pt = now_utc - PT_OFFSET
    # Find the most recent Saturday at midnight PT
    days_since_saturday = (now_pt.weekday() - 5) % 7   # 5 = Saturday
    saturday_pt = (now_pt - timedelta(days=days_since_saturday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return saturday_pt + PT_OFFSET   # back to UTC

def get_usage():
    now           = datetime.now(timezone.utc)
    month_start   = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_start    = _week_start_utc(now)
    session_start = detect_session_start()   # fixed window from actual session start

    total_month   = 0
    total_week    = 0
    total_session = 0
    daily         = defaultdict(int)

    for path in glob.glob(PROJECTS_GLOB, recursive=True):
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    msg   = d.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get("usage")
                    if not usage:
                        continue

                    tokens = (usage.get("input_tokens", 0)
                            + usage.get("cache_creation_input_tokens", 0)
                            + usage.get("cache_read_input_tokens", 0)
                            + usage.get("output_tokens", 0))
                    if not tokens:
                        continue

                    ts_str = d.get("timestamp", "")
                    ts = None
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        except Exception:
                            pass

                    # Monthly
                    if ts is None or ts >= month_start:
                        total_month += tokens
                        if ts:
                            daily[ts.astimezone().date()] += tokens

                    # Weekly (current billing week: Saturday–Saturday)
                    if ts and ts >= week_start:
                        total_week += tokens

                    # Session (fixed window from detected session start)
                    if ts and ts >= session_start:
                        total_session += tokens

        except Exception:
            continue

    return total_month, total_week, total_session, daily, session_start


def _fmt_pt_time(utc_dt):
    """Format a UTC datetime as human-readable PT time, e.g. '11:24 PM PT'."""
    pt = utc_dt - PT_OFFSET
    h  = pt.hour % 12 or 12
    am = "AM" if pt.hour < 12 else "PM"
    return f"{h}:{pt.minute:02d} {am} PT"

def _fmt_pt_date(utc_dt):
    """Format a UTC datetime as 'Apr 18' in PT."""
    pt = utc_dt - PT_OFFSET
    return pt.strftime("%b %-d")

def build_payload(settings):
    total, week_tokens, session_tokens, daily, session_start = get_usage()
    limit   = settings.get("limit", DEFAULT_LIMIT)
    now_utc = datetime.now(timezone.utc)
    now     = datetime.now()

    # Try live API first; fall back to local JSONL estimates
    has_cookie = bool(settings.get("session_key", "").strip())
    live = fetch_claude_ai_usage(settings)

    if live:
        session_pct         = live["session_pct"]
        session_reset       = live["session_reset"]
        session_reset_epoch = live["session_reset_epoch"]
        week_pct            = live["week_pct"]
        week_reset          = live["week_reset"]
        week_reset_epoch    = live["week_reset_epoch"]
        data_source         = "live"
    else:
        session_pct         = min(100.0, session_tokens / SESSION_LIMIT * 100)
        week_pct            = min(100.0, week_tokens    / WEEK_LIMIT    * 100)
        session_reset_utc   = session_start + timedelta(hours=5)
        week_reset_utc      = _week_start_utc(now_utc) + timedelta(days=7)
        session_reset       = _fmt_pt_time(session_reset_utc)
        session_reset_epoch = int(session_reset_utc.timestamp() * 1000)
        week_reset          = _fmt_pt_date(week_reset_utc)
        week_reset_epoch    = int(week_reset_utc.timestamp() * 1000)
        data_source         = "local"

    pct = min(100.0, total / limit * 100)

    # ── 7-day chart: always Mon–Sun of the current week (local timezone) ──
    _today     = datetime.now().date()
    _monday    = _today - timedelta(days=_today.weekday())   # weekday(): Mon=0
    _DAY       = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    daily_7    = [
        {"day":    _DAY[i],
         "tokens": daily.get(_monday + timedelta(days=i), 0),
         "today":  (_monday + timedelta(days=i)) == _today}
        for i in range(7)
    ]

    return {
        "pct":                 round(pct, 1),
        "used":                total,
        "limit":               limit,
        "session_tokens":      session_tokens,
        "session_pct":         round(session_pct, 1),
        "session_reset":       session_reset,
        "session_reset_epoch": session_reset_epoch,
        "week_tokens":         week_tokens,
        "week_pct":            round(week_pct, 1),
        "week_reset":          week_reset,
        "week_reset_epoch":    week_reset_epoch,
        "month":               now.strftime("%b %Y").upper(),
        "updated":             "just now",
        "interval":            settings.get("interval", 300),
        "theme":               settings.get("theme", "system"),
        "source":              data_source,
        "cookie_expired":      has_cookie and not live,
        "daily_7":             daily_7,
    }

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
        self._last_update      = None
        self._timer            = None
        self._payload          = None
        self._eff_samples      = []
        self._eff_prev_tokens  = None
        self._eff_prev_time    = None

        bar = NSStatusBar.systemStatusBar()
        self.item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        btn = self.item.button()
        btn.setTarget_(self)
        btn.setAction_(objc.selector(self.togglePopover_, signature=b"v@:@"))
        # Receive both left and right mouse-up events in the action
        btn.sendActionOn_(4 | 16)   # NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp
        self._setTitle("--%")

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
    def _calcEffRate(self, session_tokens, session_start_ms=None):
        import time as _time
        now = datetime.now()
        if (self._eff_prev_tokens is not None and
                self._eff_prev_time is not None):
            delta_tok = max(0, session_tokens - self._eff_prev_tokens)
            delta_min = (now - self._eff_prev_time).total_seconds() / 60.0
            if session_tokens < self._eff_prev_tokens * 0.5:
                self._eff_samples = []          # session reset detected
            elif delta_min >= 0.5:
                self._eff_samples.append(round(delta_tok / delta_min))
                if len(self._eff_samples) > 30:
                    self._eff_samples = self._eff_samples[-30:]
        self._eff_prev_tokens = session_tokens
        self._eff_prev_time   = now
        if not self._eff_samples:
            # Bootstrap from total session tokens ÷ session age
            if session_start_ms and session_tokens > 0:
                elapsed_min = (_time.time() * 1000 - session_start_ms) / 60000.0
                if elapsed_min >= 5:
                    return round(session_tokens / elapsed_min), round(elapsed_min)
            return None, 0
        avg = round(sum(self._eff_samples) / len(self._eff_samples))
        iv  = self.settings.get("interval", 300)
        return avg, round(len(self._eff_samples) * iv / 60)

    @objc.python_method
    def _applyPayload(self, p):
        session_tokens_eff = p.get("session_tokens", 0)
        if session_tokens_eff == 0 and p.get("session_pct", 0) > 0:
            session_tokens_eff = round(p["session_pct"] / 100.0 * SESSION_LIMIT)
        session_start_ms = p.get("session_reset_epoch", 0) - 5 * 3600 * 1000
        eff_rate, eff_min = self._calcEffRate(session_tokens_eff, session_start_ms)
        p = dict(p, eff_rate=eff_rate, eff_min=eff_min)
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

        if pct_val >= 75:
            icon_color = NSColor.systemOrangeColor()
        else:
            icon_color = NSColor.labelColor()

        from AppKit import NSTextAttachment, NSMutableAttributedString

        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "powermeter", None
        )
        result = NSMutableAttributedString.alloc().init()

        if img is not None:
            # Bold symbol via symbol configuration (weight = NSFontWeightBold = 0.4)
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
            # Build icon span and color it
            icon_as = NSMutableAttributedString.alloc().initWithAttributedString_(
                NSAttributedString.attributedStringWithAttachment_(att)
            )
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
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        appearItem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Appearance",
            objc.selector(self._menuToggleTheme_, signature=b"v@:@"),
            ""
        )
        appearItem.setTarget_(self)
        appearItem.setEnabled_(True)
        menu.addItem_(appearItem)

        menu.addItem_(NSMenuItem.separatorItem())

        removeItem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Remove",
            objc.selector(self._menuRemove_, signature=b"v@:@"),
            ""
        )
        removeItem.setTarget_(self)
        removeItem.setEnabled_(True)
        menu.addItem_(removeItem)

        event = NSApplication.sharedApplication().currentEvent()
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self.item.button())

    def _menuToggleTheme_(self, sender):
        new_theme = "light" if self.settings.get("theme", "dark") == "dark" else "dark"
        self.setTheme_(new_theme)

    def _menuRemove_(self, sender):
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
