# hackathon completeness enhancements: mismatch summary, review context, classification visibility, retry-ready states
# Requirement enhanced build: dashboard + inbox review flow + verification reporting improvements
"""Local Streamlit interface for the shipping document checker."""

from __future__ import annotations

import json
import html
from pathlib import Path
from typing import Any

import streamlit as st

from src.pipeline import validate_submission
from src.extract import FIELDS
from src.reporting import (
    PdfExportUnavailable,
    results_csv_bytes,
    results_json_bytes,
    results_pdf_bytes,
)
from src.service import (
    FIELD_LABELS,
    ProcessingArtifacts,
    UploadedAttachment,
    apply_human_review,
    comparison_rows,
    dataset_rows,
    process_dataset,
    process_single_email,
    retry_failed_email,
    submission_bytes,
    write_artifacts,
)
from src.dashboard import normalized_inbox_records, useDashboardStats, useInboxFilters


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_ROOT / "output"


def find_default_bundle() -> Path:
    candidates = (
        PROJECT_ROOT.parent / "local-data" / "participant-bundle",
        PROJECT_ROOT.parent / "sdoc-hackathon-bundle",
        PROJECT_ROOT / "data",
    )
    return next((path for path in candidates if (path / "inbox").is_dir()), candidates[1])


DEFAULT_BUNDLE = find_default_bundle()

CATEGORY_LABELS = {
    "BL_COMPARISON": "Document Comparison Request",
    "SI_REQUEST": "New SI Request",
    "INVOICE_QUERY": "Invoice Query",
    "GENERAL": "General Message",
    "SPAM": "Spam",
}


st.set_page_config(
    page_title="Shipping Document Verification",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {max-width: 1440px; padding-top: 0; padding-bottom: 4rem;}
      .stApp {background:#F6F2E9;}
      [data-testid="stSidebar"] {display:block;background:#F7F3EA;border-right:1px solid #DED3C1;}
      [data-testid="stSidebar"] > div:first-child {padding-top:1.2rem;}
      [data-testid="stSidebar"] div[role="radiogroup"] {gap:.45rem;}
      [data-testid="stSidebar"] label[data-baseweb="radio"] {background:#EEEAE3;border-radius:11px;padding:.78rem .85rem;margin:0;border:1px solid transparent;}
      [data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) {background:linear-gradient(90deg,#F9F7F2,#EDE2CD);border-color:#D7C294;box-shadow:inset 3px 0 0 #B58A39;}
      [data-testid="stSidebar"] label[data-baseweb="radio"] p {font-weight:760;color:#343842;}
      [data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] {display:block;}
      [data-testid="stSidebar"] .stButton {margin:.3rem 0;}
      [data-testid="stSidebar"] .stButton > button {width:100%;min-height:3.1rem;justify-content:flex-start;text-align:left;padding:.7rem .9rem;border-radius:12px!important;border:1px solid transparent!important;background:transparent!important;color:#161616!important;font-weight:720;box-shadow:none!important;}
      [data-testid="stSidebar"] .stButton > button:hover {border-color:#161616!important;transform:translateX(3px);}
      [data-testid="stSidebar"] .stButton > button[kind="primary"] {background:#161616!important;color:#F5F1E8!important;border-color:#161616!important;}
      [data-testid="stSidebar"] .stButton > button p {font-size:.9rem;letter-spacing:.01em;}
      [data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] {gap:.9rem!important;}
      [data-testid="stMainBlockContainer"] .stButton {margin:.15rem 0 1.15rem;}
      [data-testid="stMainBlockContainer"] div[data-testid="stMetric"] {margin-bottom:.7rem;}
      [data-testid="stMainBlockContainer"] .flow-map {margin-top:1.25rem;margin-bottom:2.25rem;}
      [data-testid="stMainBlockContainer"] .action-banner {margin-top:1.1rem;margin-bottom:1.4rem;}
      .sidebar-brand{display:flex;align-items:center;gap:.75rem;padding:.45rem .15rem 1.35rem;border-bottom:1px solid #DED3C1;margin-bottom:1.25rem}
      .sidebar-caption{font-size:.68rem;color:#A77825;font-weight:850;letter-spacing:.14em;text-transform:uppercase;margin:0 0 .55rem .2rem}
      .topbar {display:flex; justify-content:space-between; align-items:center; min-height:88px; padding:.65rem 0;}
      .brand {display:flex; align-items:center; gap:.75rem;}
      .brand-mark {width:50px; height:50px; border-radius:50%; display:grid; place-items:center; background:#101116; color:white; font-size:1.35rem;border:1px solid #C7A65A;box-shadow:0 5px 16px rgba(62,45,14,.12);}
      .brand-name {font-weight:850; color:#272A31; line-height:1.05;letter-spacing:-.03em;font-size:1.45rem;}
      .brand-sub {font-size:.72rem; color:#8A8D94; margin-top:.2rem;}
      .secure-note {font-size:.78rem; color:#0B0B14; background:#DCE9FB; border:1px solid #B7CBE8; border-radius:0; padding:.42rem .72rem;}
      .nav-caption {font-size:.76rem;color:#718096;margin:.1rem 0 .35rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;}
      .page-banner {height:245px;margin:0 0 3rem;padding:0 2rem;display:flex;align-items:center;justify-content:center;text-align:center;background:radial-gradient(circle at 52% 30%,rgba(183,143,60,.16),transparent 35%),linear-gradient(110deg,#090A0D,#1B1A17 52%,#060709);position:relative;overflow:hidden;}
      .page-banner:before,.page-banner:after{content:'';position:absolute;width:420px;height:1px;background:linear-gradient(90deg,transparent,rgba(213,188,132,.35),transparent);transform:rotate(24deg)}
      .page-banner:before{left:5%;top:36%}.page-banner:after{right:2%;bottom:28%;transform:rotate(-18deg)}
      .page-banner-title{position:relative;z-index:2;color:#fff;font-size:3.2rem;font-weight:850;letter-spacing:-.04em;}
      .page-banner-copy{position:relative;z-index:2;color:#C9C5BC;font-size:.9rem;margin-top:.55rem;letter-spacing:.02em;}
      .system-shell{background:rgba(255,255,255,.5);border:1px solid #E6DDCD;border-radius:24px;padding:1.45rem;box-shadow:0 20px 55px rgba(64,49,24,.06);}
      .home-hero {background:#E9EEF7; border:0; border-radius:0; padding:3.8rem 3.25rem; margin-bottom:1.7rem; position:relative; overflow:hidden;}
      .home-hero {display:grid;grid-template-columns:minmax(0,1.35fr) minmax(270px,.65fr);gap:2rem;align-items:center;animation:fadeUp .55s ease both;}
      .hero-visual {height:245px;position:relative;}
      .doc-sheet {position:absolute;width:170px;height:215px;border-radius:0;background:rgba(255,255,255,.94);border:2px solid #0B0B14;box-shadow:12px 12px 0 #BFD6F5;padding:1.1rem;animation:float 5s ease-in-out infinite;}
      .doc-sheet.si {left:8px;top:8px;transform:rotate(-4deg);}
      .doc-sheet.bl {right:2px;top:24px;transform:rotate(5deg);animation-delay:-2.5s;}
      .doc-tag {font-size:.68rem;font-weight:850;color:#2E6FB9;letter-spacing:.1em;}
      .doc-line {height:7px;border-radius:0;background:#D8E1ED;margin-top:.8rem;}
      .doc-line.short {width:62%;}.doc-line.alert {background:#78AEEF;width:78%;}
      .compare-mark {position:absolute;left:50%;top:44%;transform:translate(-50%,-50%);width:52px;height:52px;border-radius:50%;background:#0B0B14;color:white;display:grid;place-items:center;font-size:1.25rem;font-weight:900;box-shadow:6px 6px 0 #78AEEF;z-index:3;animation:pulse 2.8s ease-in-out infinite;}
      .eyebrow {color:#2E6FB9; font-weight:850; font-size:.76rem; letter-spacing:.14em; text-transform:uppercase;}
      .hero-title {font-size:3.3rem; line-height:1.02; letter-spacing:-.055em; color:#0B0B14; font-weight:760; max-width:760px; margin:.7rem 0 1rem;}
      .hero-copy {font-size:1.05rem; color:#373B45; max-width:680px; line-height:1.7;}
      .info-card {background:white; border:1px solid #e2e8ef; border-radius:16px; padding:1.25rem; min-height:178px; margin-bottom:1rem; box-shadow:0 6px 20px rgba(25,45,65,.04);}
      .info-number {width:32px;height:32px;border-radius:10px;background:#e3f3ef;color:#0b6b62;display:grid;place-items:center;font-weight:800;margin-bottom:.85rem;}
      .info-title {font-weight:780;color:#17364d;font-size:1.02rem;margin-bottom:.35rem;}
      .info-copy {color:#667b8c;font-size:.88rem;line-height:1.55;}
      .page-intro {margin-bottom:1.5rem;}
      .page-title {font-size:2rem;font-weight:800;color:#151927;letter-spacing:-.02em;margin:0 0 .35rem;}
      .page-copy {color:#60778a;font-size:1rem;max-width:760px;line-height:1.6;}
      .step-card {background:white;border:1px solid #e1e8ee;border-radius:16px;padding:1.25rem;margin-bottom:1rem;}
      .step-head {display:flex;gap:.8rem;align-items:center;margin-bottom:.45rem;}
      .step-badge {background:#0b5c66;color:white;width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font-weight:800;}
      .step-title {font-weight:780;color:#17364d;font-size:1.05rem;}
      .step-help {color:#667b8c;font-size:.88rem;margin-left:2.85rem;}
      .action-banner {border-radius:16px;padding:1.2rem 1.35rem;margin:.75rem 0 1.1rem;border:1px solid;}
      .action-banner.ok {background:#ecf8f3;border-color:#bfe5d5;color:#185d49;}
      .action-banner.mismatch {background:#fff0ef;border-color:#f1c3bf;color:#8f2e2a;}
      .action-banner.review {background:#fff7e6;border-color:#ecd39a;color:#805000;}
      .action-title {font-size:1.05rem;font-weight:800;margin-bottom:.25rem;}
      .action-copy {font-size:.9rem;line-height:1.5;}
      .queue-card {background:white;border:1px solid #e2e8ef;border-radius:16px;padding:1rem 1.15rem;margin:.6rem 0;}
      .queue-label {font-size:.72rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#718096;}
      .queue-count {font-size:2rem;font-weight:820;color:#102a43;line-height:1.2;margin:.25rem 0;}
      .queue-copy {font-size:.82rem;color:#667b8c;}
      .journey {display:flex;align-items:flex-start;gap:0;margin:1rem 0 2rem;}
      .journey-step {flex:1;position:relative;padding:0 1.25rem 0 0;animation:fadeUp .55s ease both;}
      .journey-step:after {content:'';position:absolute;top:17px;left:42px;right:12px;height:2px;background:linear-gradient(90deg,#78AEEF,#D7E1EF);}
      .journey-step:last-child:after {display:none;}
      .journey-dot {width:35px;height:35px;border-radius:0;background:#0B0B14;color:white;display:grid;place-items:center;font-weight:800;position:relative;z-index:2;margin-bottom:.75rem;box-shadow:4px 4px 0 #8DBBFA;}
      .journey-title {font-weight:800;color:#0B0B14;margin-bottom:.3rem;}
      .journey-copy {color:#4D5360;font-size:.86rem;line-height:1.55;max-width:260px;}
      .outcome-strip {display:flex;gap:1.1rem;flex-wrap:wrap;padding:1rem 0;border-top:1px solid #e4eaef;border-bottom:1px solid #e4eaef;}
      .outcome-item {display:flex;align-items:center;gap:.55rem;color:#526b7e;font-size:.88rem;}
      .outcome-icon {width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-weight:900;}
      .outcome-icon.ok {background:#dcf4ea;color:#14664e}.outcome-icon.bad {background:#fde5e3;color:#9d332d}.outcome-icon.wait {background:#fff0cf;color:#8b5a00}
      .queue-list {border-top:1px solid #e4eaef;margin-top:1rem;}
      .queue-row {display:grid;grid-template-columns:1fr auto;gap:1rem;align-items:center;padding:1.1rem .15rem;border-bottom:1px solid #e4eaef;}
      .queue-row-title {font-weight:800;color:#17364d}.queue-row-copy {color:#667b8c;font-size:.84rem;margin-top:.2rem}.queue-row-count {font-size:1.7rem;font-weight:850;color:#102a43;}
      .mail-pane {background:#F9FBFE;border:1px solid #C9D3E1;border-radius:0;padding:1.1rem;min-height:310px;}
      .mail-folders {margin-top:.65rem;}
      .mail-folder {display:flex;justify-content:space-between;padding:.7rem .2rem;border-bottom:1px solid #edf1f4;color:#526b7e;font-size:.88rem;}
      .mail-folder strong {color:#17364d;}
      .mail-kicker {font-size:.7rem;color:#718096;font-weight:800;letter-spacing:.1em;text-transform:uppercase;}
      .mail-subject {font-size:1.25rem;font-weight:800;color:#102a43;line-height:1.35;margin:.45rem 0 .75rem;}
      .mail-meta {font-size:.82rem;color:#6b8092;padding-bottom:.8rem;border-bottom:1px solid #e8edf1;}
      .mail-body {color:#425b70;font-size:.9rem;line-height:1.65;padding:1rem 0;white-space:pre-wrap;max-height:180px;overflow:auto;}
      .attachment-chip {display:inline-block;background:#eff4f7;color:#425b70;border-radius:8px;padding:.35rem .55rem;margin:.2rem .25rem .1rem 0;font-size:.76rem;}
      .plugin-panel {background:#0B0B14;color:white;border-radius:0;padding:1.2rem;min-height:310px;box-shadow:12px 12px 0 #8DBBFA;}
      .plugin-label {font-size:.7rem;color:#8DBBFA;font-weight:800;letter-spacing:.1em;text-transform:uppercase;}
      .plugin-status {font-size:1.25rem;font-weight:820;margin:.55rem 0;line-height:1.3;}
      .plugin-copy {color:#d4e7e6;font-size:.85rem;line-height:1.55;}
      .plugin-stat {display:flex;justify-content:space-between;border-top:1px solid rgba(255,255,255,.14);padding:.65rem 0;font-size:.82rem;}
      .plugin-stat:first-of-type {margin-top:1rem;}
      .plugin-stat span:last-child {font-weight:800;color:white;}
      .flow-map {display:flex;align-items:stretch;margin:1rem 0 2rem;background:#FCFAF6;border:1px solid #E2D8C6;border-radius:18px;overflow:hidden;}
      .flow-node {flex:1;padding:1.25rem;position:relative;min-height:125px;}
      .flow-node + .flow-node {border-left:1px solid #E2D8C6;}
      .flow-node + .flow-node:before {content:'→';position:absolute;left:-13px;top:43px;width:26px;height:26px;background:#B78A35;color:white;display:grid;place-items:center;border-radius:50%;font-size:.8rem;}
      .flow-number {font-size:2rem;font-weight:850;color:#0B0B14;line-height:1;}
      .flow-label {font-size:.76rem;font-weight:850;letter-spacing:.08em;text-transform:uppercase;color:#A77825;margin:.45rem 0 .3rem;}
      .flow-copy {font-size:.78rem;color:#515968;line-height:1.45;}
      .task-list {border-top:2px solid #0B0B14;margin-top:.7rem;}
      .task-row {display:grid;grid-template-columns:44px 1fr auto;gap:.85rem;align-items:center;padding:1rem .25rem;border-bottom:1px solid #CDD6E2;}
      .task-icon {width:34px;height:34px;background:#DCE9FB;display:grid;place-items:center;font-weight:900;color:#0B0B14;}
      .task-name {font-weight:820;color:#0B0B14;}.task-help {font-size:.8rem;color:#626A78;margin-top:.18rem;}.task-count{font-size:1.4rem;font-weight:850;color:#0B0B14;}
      .case-head {padding:1.3rem 0;border-top:2px solid #0B0B14;border-bottom:1px solid #CDD6E2;margin-bottom:1rem;}
      .case-action {font-size:1.65rem;font-weight:850;letter-spacing:-.03em;color:#0B0B14;}
      .case-subject {font-size:.9rem;color:#565E6C;margin-top:.35rem;}
      .case-meta {display:flex;gap:1rem;flex-wrap:wrap;margin-top:.8rem;font-size:.78rem;color:#4D5563;}
      .mini-pill {background:#EFE7D8;padding:.3rem .5rem;}
      .side-menu{background:#fff;border:1px solid #E9E1D4;border-radius:20px;padding:1.15rem;margin-bottom:1rem;box-shadow:0 12px 30px rgba(60,43,17,.05)}
      .side-menu-title{font-size:.68rem;color:#A77825;font-weight:850;letter-spacing:.14em;text-transform:uppercase;margin:.2rem .35rem .8rem}
      .side-menu-item{padding:.78rem .9rem;border-radius:11px;background:#F7F5F1;margin:.45rem 0;color:#5D626C;font-size:.86rem;font-weight:720;display:flex;justify-content:space-between;align-items:center}
      .side-menu-item.active{background:linear-gradient(90deg,#F7F5F1,#EEE3CF);color:#171B27;box-shadow:inset 3px 0 0 #B88B37}
      .side-menu-badge{min-width:26px;height:26px;padding:0 .4rem;border-radius:13px;background:#fff;display:grid;place-items:center;color:#9A722B;font-size:.72rem}
      @media (max-width:800px){.flow-map{display:block}.flow-node + .flow-node{border-left:0;border-top:1px solid #C9D3E1}.flow-node + .flow-node:before{display:none}}
      @keyframes fadeUp {from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:translateY(0)}}
      @keyframes float {0%,100%{translate:0 0}50%{translate:0 -8px}}
      @keyframes pulse {0%,100%{box-shadow:6px 6px 0 #78AEEF}50%{box-shadow:10px 10px 0 #78AEEF}}
      @media (max-width:800px){.home-hero{grid-template-columns:1fr;padding:2rem}.hero-visual{display:none}.hero-title{font-size:2.15rem}.journey{display:block}.journey-step{padding:0 0 1.35rem 0}.journey-step:after{display:none}.secure-note{display:none}}
      .section-label {font-size:.76rem; color:#557088; font-weight:750; letter-spacing:.12em; text-transform:uppercase; margin:1.5rem 0 .4rem;}
      .result-note {padding: .9rem 1rem; border-radius: .75rem; background: #f5f8fb; border-left: 4px solid #557088;}
      .status-track {display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; margin:.5rem 0 1.25rem;}
      .status-step {padding:.5rem .8rem; border-radius:999px; background:#eaf0f5; color:#425b70; font-size:.78rem; font-weight:700;}
      .status-step.done {background:#dff5ee; color:#12664f;}
      .status-step.review {background:#fff0d5; color:#8a5200;}
      .status-step.failed {background:#fee6e5; color:#a32d2a;}
      .status-arrow {color:#8da0b1;}
      .confidence-wrap {background:#f5f8fb; border:1px solid #dce5ef; border-radius:12px; padding:.9rem 1rem;}
      .evidence-card {background:#fff; border:1px solid #dce5ef; border-radius:12px; padding:1rem; min-height:145px;}
      .evidence-meta {color:#6b8092; font-size:.78rem; margin-bottom:.55rem;}
      .evidence-value {font-weight:700; color:#102a43; margin-bottom:.55rem;}
      .evidence-quote {background:#f5f8fb; padding:.65rem; border-radius:8px; color:#425b70; font-family:monospace; font-size:.8rem; white-space:pre-wrap;}
      div[data-testid="stMetric"] {background: #fff; border: 1px solid #dce5ef; padding: .9rem; border-radius: 12px; box-shadow:0 3px 12px rgba(15,43,67,.04);}
      div[data-testid="stDataFrame"] {border:1px solid #dce5ef; border-radius:12px; overflow:hidden;}
      .stButton > button[kind="primary"] {border-radius:12px; font-weight:800; min-height:2.8rem;background:#B58A39;border-color:#B58A39;color:white;box-shadow:none;}
      .stButton > button {border-radius:12px; font-weight:750;border:1px solid #D9C9A8;background:#FCFAF6;}
      .stDownloadButton > button {border-radius:0; font-weight:800;}
      header[data-testid="stHeader"] {background:transparent;height:0;}
      #MainMenu {visibility:hidden;}
      footer {visibility:hidden;}

      /* Two-colour system: warm canvas + ink. Meaning comes from type, shape and icons. */
      .stApp,[data-testid="stAppViewContainer"],.main {background:#F5F1E8!important;color:#161616!important;}
      [data-testid="stSidebar"] {background:#F5F1E8!important;border-right:1px solid #161616!important;}
      [data-testid="stSidebar"] label[data-baseweb="radio"] {background:#F5F1E8!important;border:1px solid #161616!important;}
      [data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) {background:#161616!important;border-color:#161616!important;box-shadow:none!important;}
      [data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) p {color:#F5F1E8!important;}
      .brand-mark,.task-icon,.journey-dot,.compare-mark {background:#161616!important;color:#F5F1E8!important;border-color:#161616!important;box-shadow:none!important;}
      .brand-name,.page-title,.info-title,.queue-row-title,.queue-row-count,.flow-number,.task-name,.task-count,.case-action {color:#161616!important;}
      .brand-sub,.page-copy,.info-copy,.flow-copy,.task-help,.case-subject,.case-meta,.queue-row-copy {color:#161616!important;opacity:.68;}
      .eyebrow,.section-label,.sidebar-caption,.side-menu-title,.flow-label,.doc-tag {color:#161616!important;}
      .page-banner {background:#161616!important;}
      .page-banner:before,.page-banner:after {background:#F5F1E8!important;opacity:.16;}
      .page-banner-title,.page-banner-copy {color:#F5F1E8!important;}
      .info-card,.queue-card,.mail-pane,.evidence-card,.confidence-wrap,div[data-testid="stMetric"],.flow-map,.side-menu {background:#F5F1E8!important;border-color:#161616!important;box-shadow:none!important;}
      .step-card {background:#F5F1E8!important;border-color:#161616!important;box-shadow:none!important;}
      .step-badge {background:#161616!important;color:#F5F1E8!important;}
      .step-title,.step-help {color:#161616!important;}
      [data-testid="stFileUploaderDropzone"] {background:#F5F1E8!important;border-color:#161616!important;}
      [data-testid="stFileUploaderDropzone"] button {background:#F5F1E8!important;border-color:#161616!important;color:#161616!important;}
      [data-testid="stFileUploaderDropzone"] svg {color:#161616!important;fill:#161616!important;}
      .flow-node + .flow-node,.task-row,.case-head,.queue-row,.outcome-strip {border-color:#161616!important;}
      .flow-node + .flow-node:before,.mini-pill,.info-number,.outcome-icon,.status-step,.action-banner {background:#161616!important;color:#F5F1E8!important;border-color:#161616!important;}
      .action-banner *,.status-step * {color:#F5F1E8!important;}
      .stButton > button[kind="primary"],.stDownloadButton > button[kind="primary"] {background:#161616!important;border-color:#161616!important;color:#F5F1E8!important;box-shadow:none!important;}
      .stButton > button:not([kind="primary"]),.stDownloadButton > button {background:#F5F1E8!important;border-color:#161616!important;color:#161616!important;}
      div[data-testid="stDataFrame"] {border-color:#161616!important;}
      [data-testid="stAlert"] {background:#F5F1E8!important;color:#161616!important;border:1px solid #161616!important;}
      code {color:#161616!important;background:#F5F1E8!important;border:1px solid #161616!important;}
      .action-banner.ok {background:#E4F3EF!important;border-color:#0B6B63!important;color:#0B6B63!important;}
      .action-banner.ok * {color:#0B6B63!important;}
      .action-banner.mismatch {background:#FBE9E7!important;border-color:#A4362F!important;color:#A4362F!important;}
      .action-banner.mismatch * {color:#A4362F!important;}
      .action-banner.review {background:#FFF3D8!important;border-color:#7B5B16!important;color:#7B5B16!important;}
      .action-banner.review * {color:#7B5B16!important;}
      .result-mode-head {display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid #161616;padding-bottom:1rem;margin-bottom:1.4rem;}
      .result-mode-title {font-size:2rem;font-weight:850;letter-spacing:-.03em;color:#161616;}
      .result-mode-copy {font-size:.88rem;color:#161616;opacity:.68;margin-top:.25rem;}
      .bucket-copy {min-height:118px;border:1px solid #161616;border-radius:16px;padding:1.15rem;margin-top:.65rem;display:flex;flex-direction:column;justify-content:space-between;}
      .bucket-copy b {font-size:1.8rem;color:#161616;}
      .bucket-copy span {font-size:.82rem;color:#161616;opacity:.68;line-height:1.45;}
      .files-found {display:flex;justify-content:space-between;align-items:center;gap:1rem;border-top:1px solid #161616;border-bottom:1px solid #161616;padding:1rem .15rem;margin:1.4rem 0 1rem;}
      .files-found b {font-size:1.15rem;color:#161616;}.files-found span {font-size:.82rem;color:#161616;opacity:.66;}
      .file-list-heading {font-size:.72rem;font-weight:850;letter-spacing:.08em;text-transform:uppercase;color:#161616;opacity:.62;padding:.15rem .1rem .45rem;}
      .file-list-id {font-size:.86rem;font-weight:820;color:#161616;padding:.72rem .1rem;white-space:nowrap;}
      .file-list-subject {font-size:.86rem;color:#161616;padding:.72rem .1rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
      .file-status {display:inline-block;margin-top:.55rem;padding:.28rem .55rem;border:1px solid #161616;border-radius:999px;font-size:.72rem;font-weight:820;white-space:nowrap;}
      .file-status.mismatch {background:#FBE9E7;border-color:#A4362F;color:#A4362F;}
      .file-status.review {background:#FFF3D8;border-color:#7B5B16;color:#7B5B16;}
      .file-status.passed {background:#E4F3EF;border-color:#0B6B63;color:#0B6B63;}
      .file-list-rule {height:1px;background:#161616;opacity:.18;margin:-.15rem 0 .15rem;}
    
.dashboard-card {
    background:#F5F1E8;
    border:1px solid #161616;
    border-radius:18px;
    padding:1.25rem;
    min-height:150px;
}
.dashboard-number {
    font-size:2.4rem;
    font-weight:850;
    color:#161616;
    margin:.5rem 0;
}
.dashboard-label {
    font-size:.8rem;
    font-weight:800;
    letter-spacing:.08em;
    text-transform:uppercase;
    opacity:.65;
}
.dashboard-progress {
    height:8px;
    background:#DED8CC;
    border-radius:10px;
    overflow:hidden;
    margin-top:.8rem;
}
.dashboard-progress span {
    display:block;
    height:100%;
    background:#161616;
}
.alert-card {
    border-radius:18px;
    padding:1.2rem;
    border:1px solid #161616;
    min-height:130px;
}
.timeline-item {
    border-left:2px solid #161616;
    padding-left:1rem;
    margin:.8rem 0;
}


.requirement-card {
    background:#F5F1E8;
    border:1px solid #161616;
    border-radius:18px;
    padding:1rem;
    margin:.5rem 0;
}
.requirement-title {
    font-weight:850;
    margin-bottom:.4rem;
}
.requirement-copy {
    opacity:.75;
    line-height:1.45;
}
.mismatch-row {
    padding:.8rem;
    border-radius:12px;
    background:#FFF0F0;
    border:1px solid #D92D20;
    margin:.5rem 0;
}
.review-row {
    padding:.8rem;
    border-radius:12px;
    background:#FFF8E1;
    border:1px solid #B54708;
    margin:.5rem 0;
}


.verification-section-title {
    font-size:1.15rem;
    font-weight:850;
    letter-spacing:-0.02em;
    color:#161616;
    margin:1.5rem 0 .6rem;
}
.verification-main-status {
    font-size:2rem;
    font-weight:850;
    letter-spacing:-0.04em;
    color:#161616;
    line-height:1.15;
}
.verification-card-label {
    font-size:.72rem;
    font-weight:850;
    letter-spacing:.08em;
    text-transform:uppercase;
    color:#161616;
    opacity:.65;
}
.verification-card-value {
    font-size:1rem;
    font-weight:800;
    color:#161616;
    margin-top:.35rem;
}


/* Dashboard spacing refinement */
.page-title {
    margin-bottom: 0.45rem !important;
}

.page-copy {
    margin-bottom: 1.6rem !important;
}

.section-label {
    margin-top: 1.35rem !important;
    margin-bottom: 0.7rem !important;
}

h1 {
    margin-bottom: 0.5rem !important;
}

h2 {
    margin-top: 1.1rem !important;
    margin-bottom: 0.75rem !important;
}

h3 {
    margin-top: 0.9rem !important;
    margin-bottom: 0.6rem !important;
}


       .journey {
           display:flex;
           align-items:flex-start;
           gap:0;
           margin:2rem auto 2.25rem;
           max-width:1100px;
           justify-content:center;
       }
       .journey-step {
           padding:0 1.25rem;
       }

</style>
    """,
    unsafe_allow_html=True,
)


def uploaded_attachment(uploaded_file: Any) -> UploadedAttachment | None:
    if uploaded_file is None:
        return None
    return UploadedAttachment(uploaded_file.name, uploaded_file.getvalue())


def load_email_json(uploaded_file: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(uploaded_file.getvalue().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Email file must be valid UTF-8 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Email JSON must contain one object")
    return parsed


def section_label(text: str) -> None:
    st.markdown(f'<div class="section-label">{text}</div>', unsafe_allow_html=True)


def render_status_history(detail: dict[str, Any]) -> None:
    history = detail.get("status_history", [])

    # Create a default lifecycle when backend history is unavailable
    if not history:
        final_status = detail.get("task_status", "Completed")

        if final_status == "Failed":
            history = [
                {"status": "Received"},
                {"status": "Processing"},
                {"status": "Failed"},
            ]
        elif final_status == "Review":
            history = [
                {"status": "Received"},
                {"status": "Processing"},
                {"status": "Review"},
            ]
        else:
            history = [
                {"status": "Received"},
                {"status": "Processing"},
                {"status": "Completed"},
            ]

    parts = []

    for index, item in enumerate(history):
        status = item.get("status", "Processing")

        css = "done"
        if status == "Review":
            css = "review"
        elif status == "Failed":
            css = "failed"

        parts.append(
            f'<span class="status-step {css}">{status}</span>'
        )

        if index < len(history) - 1:
            parts.append('<span class="status-arrow">→</span>')

    st.markdown(
        f'<div class="status-track">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )


def render_confidence(detail: dict[str, Any]) -> None:
    classification = float(detail.get("classification_confidence", 0))
    threshold = float(detail.get("confidence_threshold", .75))
    label = "High confidence" if classification >= .9 else "Moderate confidence" if classification >= threshold else "Human review"
    section_label("Confidence & uncertainty")
    left, right = st.columns([1, 2])
    left.metric("Classification confidence", f"{classification:.0%}")
    right.markdown(
        f'<div class="confidence-wrap"><b>{label}</b><br><span style="color:#60778a">{detail.get("classification_rule", "No rule recorded")}</span><br><small>Human review threshold: {threshold:.0%}</small></div>',
        unsafe_allow_html=True,
    )


def render_evidence(detail: dict[str, Any]) -> None:
    evidence = detail.get("evidence", {})
    extracted = detail.get("extracted", {})
    if not evidence:
        return
    section_label("Source evidence")
    st.caption("Every extracted value remains traceable to its source document and line.")
    for row in comparison_rows(detail):
        field_key = next((key for key, label in FIELD_LABELS.items() if label == row["Field"]), None)
        if field_key is None:
            continue
        with st.expander(f'{row["Field"]}  ·  {row["Status"]}  ·  {row["Confidence"]}'):
            columns = st.columns(2)
            for column, role, label in ((columns[0], "si", "Shipping Instruction"), (columns[1], "bl", "Draft Bill of Lading")):
                item = evidence.get(role, {}).get(field_key, {})
                value = extracted.get(role, {}).get(field_key, "—")
                location = f'Page {item.get("page", 1)} · Line {item.get("line_start", "—")}'
                if item.get("line_end") != item.get("line_start"):
                    location += f'–{item.get("line_end")}'
                column.markdown(
                    f'<div class="evidence-card"><b>{label}</b><div class="evidence-meta">{html.escape(str(item.get("source", "Unknown source")))} · {location} · {item.get("confidence", 0):.0%}</div><div class="evidence-value">{html.escape(str(value))}</div><div class="evidence-quote">{html.escape(str(item.get("excerpt", "No excerpt available")))}</div></div>',
                    unsafe_allow_html=True,
                )


def render_document_types(detail: dict[str, Any]) -> None:
    """Show what each attachment was detected as, judged by content and not by filename."""
    analysis = detail.get("document_analysis") or {}
    detections = analysis.get("detections") or {}
    if not detections:
        return
    rows = []
    for path, det in detections.items():
        if det.get("reason") == "no_text":
            note = "No readable text (corrupt file or scanned image)"
        elif det.get("reason") == "conflicting_titles":
            note = "Conflicting titles: " + ", ".join(det.get("conflicts") or [])
        elif det.get("reason") == "no_title_found":
            note = "No document title found"
        elif det.get("type") not in ("SI", "BL"):
            note = "Not a Shipping Instruction or Bill of Lading"
        elif det.get("filename_hint") not in (None, det["type"]):
            note = f"Filename suggests {det['filename_hint']}, content says {det['type']}"
        else:
            note = "As expected"
        rows.append(
            {
                "File": Path(path).name,
                "Detected as": det.get("label") or "Unknown",
                "Confidence": f"{det.get('confidence', 0):.0%}" if det.get("confidence") else "—",
                "Note": note,
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")
    if analysis.get("swapped"):
        st.info("The SI and BL were in the wrong places. Roles were assigned from the document titles, not the filenames.")


def render_single_result(artifacts: ProcessingArtifacts) -> None:
    email=artifacts.emails[0]
    email_id=email["email_id"]
    result=artifacts.submission[email_id]
    detail=artifacts.internal_results[email_id]

    status=result["status"]
    status_map={"MISMATCH":"🔴 Mismatch detected","NEEDS_REVIEW":"🟡 Human review required","OK":"🟢 Passed"}

    section_label("Verification result")
    st.markdown(
        f'<div class="verification-main-status">{status_map.get(status,status)}</div>',
        unsafe_allow_html=True
    )
    st.caption(email.get("subject","Untitled"))

    section_label("Task status")
    render_status_history(detail)

    if status=="MISMATCH":
        st.error("Action required: Correct the draft Bill of Lading.")
    elif status=="NEEDS_REVIEW":
        st.warning("Action required: Assign reviewer before approval.")
    else:
        st.success("No action required.")

    section_label("Summary")
    cols=st.columns(4)
    data=[("Email ID",email_id),("Sender",email.get("from","-")),("Category",CATEGORY_LABELS[result["category"]]),("Confidence",f"{detail.get('classification_confidence',0):.0%}")]
    for c,(l,v) in zip(cols,data):
        with c:
            st.markdown(
                f"""
                <div class="queue-card">
                    <div class="verification-card-label">{html.escape(str(l))}</div>
                    <div class="verification-card-value">{html.escape(str(v))}</div>
                </div>
                """,
                unsafe_allow_html=True
            )

    if (detail.get("document_analysis") or {}).get("detections"):
        section_label("Documents")
        render_document_types(detail)

    if status=="MISMATCH":
        section_label("Mismatch details")
        for m in detail.get("mismatches",[]):
            st.warning(f"{m['field']}\n\nSI: {m['si_value']}\n\nBL: {m['bl_value']}")

    section_label("Shipment comparison")
    st.dataframe(comparison_rows(detail),hide_index=True,width="stretch")

    section_label("Evidence")
    render_evidence(detail)

def run_dataset_with_progress(bundle_path: str) -> ProcessingArtifacts:
    progress = st.progress(0.0)
    status_text = st.empty()
    live_stage = st.status("Opening inbox...", expanded=True)

    def update(completed: int, total: int, email_id: str) -> None:
        ratio = completed / total if total else 1.0
        progress.progress(min(ratio, 1.0))
        if completed < total:
            status_text.caption(f"Processing {email_id} · {completed}/{total} completed")
        else:
            status_text.caption(f"Completed {completed}/{total} emails")

    def update_stage(email_id: str, stage: str) -> None:
        live_stage.update(label=f"{stage} · {email_id}", state="running")

    try:
        artifacts = process_dataset(bundle_path, update, update_stage)
    except Exception:
        live_stage.update(label="Inbox processing failed", state="error", expanded=True)
        raise
    live_stage.update(label="Inbox analysis completed", state="complete", expanded=False)
    return artifacts


def render_summary(artifacts: ProcessingArtifacts) -> None:
    records = normalized_inbox_records(
        artifacts.emails, artifacts.submission, artifacts.internal_results
    )
    stats = useDashboardStats(records)
    cards = (
        ("Total emails processed", f'{stats["total_emails"]:,}', "Inbox records"),
        ("Comparison cases", f'{stats["comparison_cases"]:,}', "SI and BL checks"),
        ("Mismatch count", f'{stats["mismatch_count"]:,}', "Fields requiring correction"),
        ("Human review", f'{stats["human_review_count"]:,}', "Low confidence or incomplete"),
        ("Failed cases", f'{stats["failed_cases"]:,}', "Processing or attachment errors"),
        ("Success rate", f'{stats["success_rate"]:.0%}', "Automatically resolved comparisons"),
    )
    columns = st.columns(3)
    for index, (label, value, caption) in enumerate(cards):
        with columns[index % 3]:
            st.markdown(
                f'<div class="dashboard-card"><div class="dashboard-label">{label}</div><div class="dashboard-number">{value}</div><div style="opacity:.65">{caption}</div></div>',
                unsafe_allow_html=True,
            )


def render_dataset_table(artifacts: ProcessingArtifacts) -> None:
    rows = dataset_rows(artifacts)
    st.subheader("Results")
    left, right = st.columns(2)
    categories = sorted({row["category"] for row in rows})
    selected_categories = left.multiselect("Category", categories, default=categories)
    outcome = right.selectbox(
        "Outcome",
        ["All", "Mismatch", "No Mismatch", "Human Review"],
    )

    filtered = [row for row in rows if row["category"] in selected_categories]
    if outcome == "Mismatch":
        filtered = [row for row in filtered if row["comparison status"] == "MISMATCH"]
    elif outcome == "No Mismatch":
        filtered = [
            row
            for row in filtered
            if row["category"] == "BL_COMPARISON" and row["comparison status"] == "OK"
        ]
    elif outcome == "Human Review":
        filtered = [row for row in filtered if row["comparison status"] == "NEEDS_REVIEW"]
    st.caption(f"Showing {len(filtered)} of {len(rows)} emails")
    st.dataframe(filtered, hide_index=True, width="stretch", height=520)


def single_case_artifacts(artifacts: ProcessingArtifacts, email_id: str) -> ProcessingArtifacts:
    email = next(email for email in artifacts.emails if email["email_id"] == email_id)
    return ProcessingArtifacts(
        [email],
        {email_id: artifacts.submission[email_id]},
        {email_id: artifacts.internal_results[email_id]},
        artifacts.summary,
    )


def render_action_center(artifacts: ProcessingArtifacts) -> None:
    mismatches = [email_id for email_id, row in artifacts.submission.items() if row["status"] == "MISMATCH"]
    reviews = [email_id for email_id, row in artifacts.submission.items() if row["status"] == "NEEDS_REVIEW"]
    cleared = [
        email_id
        for email_id, row in artifacts.submission.items()
        if row["category"] == "BL_COMPARISON" and row["status"] == "OK"
    ]
    email_by_id = {email["email_id"]: email for email in artifacts.emails}

    section_label("Work queue")
    st.markdown("### Start with the cases that need attention")
    st.caption("Mismatch cases should be corrected. Review cases need a person to inspect the source documents. Cleared cases require no action.")
    queue_data = (
        ("Mismatch — correct BL", len(mismatches), "Document values differ from the approved SI."),
        ("Human review", len(reviews), "Missing, unreadable, or uncertain information."),
        ("Cleared", len(cleared), "All seven fields agree; no action required."),
    )
    queue_html = "".join(
        f'<div class="queue-row"><div><div class="queue-row-title">{label}</div><div class="queue-row-copy">{copy}</div></div><div class="queue-row-count">{count}</div></div>'
        for label, count, copy in queue_data
    )
    st.markdown(f'<div class="queue-list">{queue_html}</div>', unsafe_allow_html=True)

    attention = mismatches + reviews
    if attention:
        section_label("Review a case")
        filter_col, case_col = st.columns([1, 2])
        queue_filter = filter_col.selectbox("Show", ["All action required", "Mismatch only", "Human review only"])
        choices = attention
        if queue_filter == "Mismatch only":
            choices = mismatches
        elif queue_filter == "Human review only":
            choices = reviews

        def case_label(email_id: str) -> str:
            row = artifacts.submission[email_id]
            subject = str(email_by_id[email_id].get("subject", "Untitled"))
            action = "Correct BL" if row["status"] == "MISMATCH" else "Review"
            return f"{action} · {email_id} · {subject}"

        selected = case_col.selectbox("Choose a case", choices, format_func=case_label)
        with st.expander("Open selected case", expanded=True):
            render_single_result(single_case_artifacts(artifacts, selected))

    with st.expander("View all email records"):
        st.caption("This detailed table is for searching, filtering, and audit. Daily work should begin in the action queue above.")
        render_dataset_table(artifacts)


def render_inbox_workspace(artifacts: ProcessingArtifacts) -> None:
    email_by_id = {email["email_id"]: email for email in artifacts.emails}
    records = normalized_inbox_records(
        artifacts.emails, artifacts.submission, artifacts.internal_results
    )

    mismatches = [k for k, v in artifacts.submission.items() if v["status"] == "MISMATCH"]
    failed = [
        k for k, detail in artifacts.internal_results.items()
        if detail.get("processing_status", detail.get("task_status")) == "Failed"
    ]
    reviews = [
        k for k, v in artifacts.submission.items()
        if v["status"] == "NEEDS_REVIEW" and k not in failed
    ]
    cleared = [
        k for k, v in artifacts.submission.items()
        if v["category"] == "BL_COMPARISON" and v["status"] == "OK"
    ]

    st.session_state.setdefault("inbox_bucket", "Mismatches")
    st.session_state.setdefault("inbox_selected_id", None)

    def choose_bucket(name):
        st.session_state.inbox_bucket = name
        st.session_state.inbox_selected_id = None

    def choose_file(email_id):
        if st.session_state.inbox_selected_id == email_id:
            st.session_state.inbox_selected_id = None
        else:
            st.session_state.inbox_selected_id = email_id

    section_label("Analysis results")

    buckets = [
        ("All files", f"• All files ({len(artifacts.emails)})", list(email_by_id)),
        ("Mismatches", f"! Mismatch ({len(mismatches)})", mismatches),
        ("Human review", f"? Human Review ({len(reviews)})", reviews),
        ("Failed", f"× Failed ({len(failed)})", failed),
        ("Passed", f"✓ Passed ({len(cleared)})", cleared),
    ]

    cols = st.columns(5)

    for col, (name, label, _) in zip(cols, buckets):
        with col:
            st.button(
                label,
                key=f"bucket_{name}",
                type="primary" if st.session_state.inbox_bucket == name else "secondary",
                width="stretch",
                on_click=choose_bucket,
                args=(name,),
            )

    choices = {
        "All files": list(email_by_id),
        "Mismatches": mismatches,
        "Human review": reviews,
        "Failed": failed,
        "Passed": cleared,
    }[st.session_state.inbox_bucket]

    st.markdown(
        f'<div class="files-found"><b>{len(choices)} files found</b><span>Select a file to expand details.</span></div>',
        unsafe_allow_html=True,
    )

    search_col, status_col, type_col, mismatch_col = st.columns([2, 1, 1, 1])
    search = search_col.text_input(
        "Search files",
        placeholder="Search by email ID or subject",
        key=f"search_{st.session_state.inbox_bucket}",
    )
    selected_status = status_col.selectbox(
        "Status", ["All", "Processing", "Completed", "Review Required", "Failed"],
        key=f"status_{st.session_state.inbox_bucket}",
    )
    selected_type = type_col.selectbox(
        "Document type", ["All", "SI", "BL", "Invoice", "Unknown"],
        key=f"document_type_{st.session_state.inbox_bucket}",
    )
    selected_mismatch = mismatch_col.selectbox(
        "Mismatch", ["All", "Has Mismatches", "All Fields Match"],
        key=f"mismatch_{st.session_state.inbox_bucket}",
    )
    filtered_records = useInboxFilters(
        records,
        {"status": selected_status, "document_type": selected_type, "mismatch_status": selected_mismatch},
        search,
    )
    filtered = [email_id for email_id in choices if any(
        record["email_id"] == email_id for record in filtered_records
    )]

    for email_id in filtered[:20]:

        email = email_by_id[email_id]
        subject = str(email.get("subject", "Untitled"))
        result = artifacts.submission[email_id]

        detail = artifacts.internal_results[email_id]
        if detail.get("processing_status", detail.get("task_status")) == "Failed":
            status = "Failed"
            css = "mismatch"
        elif result["status"] == "MISMATCH":
            status = "Mismatch"
            css = "mismatch"
        elif result["status"] == "NEEDS_REVIEW":
            status = "Human Review"
            css = "review"
        else:
            status = "Passed"
            css = "passed"

        expanded = st.session_state.inbox_selected_id == email_id

        row = st.container(border=True)

        with row:
            st.markdown(
                f"""
                **{html.escape(email_id)}**

                {html.escape(subject)}

                <span class="file-status {css}">{status}</span>
                """,
                unsafe_allow_html=True,
            )

            st.button(
                "Collapse" if expanded else "View details",
                key=f"view_{email_id}",
                type="primary" if expanded else "secondary",
                width="stretch",
                on_click=choose_file,
                args=(email_id,),
            )

            # IMPORTANT: detail appears directly under the selected item
            if expanded:
                st.markdown("### Verification details")

                tab1, tab2, tab3 = st.tabs(
                    ["Analysis result", "Source evidence", "Action"]
                )

                with tab1:
                    st.dataframe(
                        comparison_rows(detail),
                        hide_index=True,
                        width="stretch",
                    )
                    render_confidence(detail)

                with tab2:
                    render_document_types(detail)
                    st.write(f"**From:** {email.get('from', 'Unknown')}")
                    st.write(email.get("body", "No message body"))
                    render_evidence(detail)

                with tab3:
                    st.write(
                        f"Status: {result['status'].replace('_',' ').title()}"
                    )
                    if result.get("review_reason"):
                        st.write(result["review_reason"])
                    if detail.get("error_history"):
                        latest = detail["error_history"][-1]
                        st.error(f"{latest.get('type', 'Error')}: {latest.get('message', 'Unknown processing error')}")
                        st.caption(
                            f"Attempts: {detail.get('processing_attempts', 1)} · "
                            f"Retries: {detail.get('retry_count', 0)}"
                        )

    if not filtered:
        st.info("No files match this search.")


def render_error_history(detail: dict[str, Any]) -> None:
    errors = detail.get("error_history", [])
    if not errors:
        st.info("No processing errors were recorded for this case.")
        return
    for index, item in enumerate(reversed(errors), start=1):
        with st.expander(
            f"Error {len(errors) - index + 1} · {item.get('type', 'Error')} · {item.get('at', 'Unknown time')}",
            expanded=index == 1,
        ):
            st.code(item.get("message", "Unknown processing error"), language=None)
            if item.get("traceback"):
                st.code(item["traceback"], language="text")


def render_human_review_dashboard(
    artifacts: ProcessingArtifacts, dataset_bundle: str
) -> None:
    email_by_id = {email["email_id"]: email for email in artifacts.emails}
    failed = [
        email_id
        for email_id, detail in artifacts.internal_results.items()
        if detail.get("processing_status", detail.get("task_status")) == "Failed"
    ]
    pending = [
        email_id
        for email_id, result in artifacts.submission.items()
        if result["status"] == "NEEDS_REVIEW" and email_id not in failed
    ]
    resolved = [
        email_id
        for email_id, detail in artifacts.internal_results.items()
        if detail.get("review_state") in {"confirmed", "corrected"}
    ]

    if st.session_state.pop("review_notice", None):
        st.success("Human review saved and result recalculated.")
    if st.session_state.pop("retry_notice", None):
        st.success("Retry completed. The case status and error history were updated.")

    metrics = st.columns(3)
    metrics[0].metric("Pending review", len(pending))
    metrics[1].metric("Failed processing", len(failed))
    metrics[2].metric("Reviewed", len(resolved))

    review_tab, failed_tab, resolved_tab = st.tabs(
        ["Manual verification", "Processing errors", "Completed reviews"]
    )

    with review_tab:
        if not pending:
            st.success("No cases are waiting for manual verification.")
        else:
            selected = st.selectbox(
                "Choose a case",
                pending,
                format_func=lambda email_id: (
                    f"{email_id} · {email_by_id[email_id].get('subject', 'Untitled')}"
                ),
                key="human_review_case",
            )
            result = artifacts.submission[selected]
            detail = artifacts.internal_results[selected]
            email = email_by_id[selected]
            st.caption(
                f"Reason: {result.get('review_reason') or detail.get('internal_reason') or 'Verification required'}"
            )
            st.write(email.get("body", ""))
            st.dataframe(comparison_rows(detail), hide_index=True, width="stretch")
            st.markdown("### Confirm or correct extracted values")
            st.caption(
                "SI is the reference. Enter all seven SI and BL values, then save the review. "
                "The comparison result will be recalculated without changing the official schema."
            )
            extracted = detail.get("extracted", {})
            with st.form(f"review_form_{selected}"):
                header = st.columns([1.2, 2, 2])
                header[0].markdown("**Field**")
                header[1].markdown("**SI value**")
                header[2].markdown("**BL value**")
                si_values: dict[str, str] = {}
                bl_values: dict[str, str] = {}
                for field in FIELDS:
                    columns = st.columns([1.2, 2, 2])
                    columns[0].write(FIELD_LABELS[field])
                    si_values[field] = columns[1].text_input(
                        f"SI {FIELD_LABELS[field]}",
                        value=str(extracted.get("si", {}).get(field, "") or ""),
                        label_visibility="collapsed",
                        key=f"review_{selected}_si_{field}",
                    )
                    bl_values[field] = columns[2].text_input(
                        f"BL {FIELD_LABELS[field]}",
                        value=str(extracted.get("bl", {}).get(field, "") or ""),
                        label_visibility="collapsed",
                        key=f"review_{selected}_bl_{field}",
                    )
                note = st.text_area(
                    "Reviewer note",
                    placeholder="Optional note explaining the confirmation or correction",
                    key=f"review_note_{selected}",
                )
                save_review = st.form_submit_button(
                    "Save review and recalculate", type="primary", width="stretch"
                )
            if save_review:
                try:
                    updated = apply_human_review(
                        artifacts, selected, si_values, bl_values, note
                    )
                    st.session_state.dataset_artifacts = updated
                    st.session_state.submission_ready = False
                    st.session_state.pop("export_files", None)
                    write_artifacts(updated, DEFAULT_OUTPUT)
                    st.session_state.review_notice = True
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    with failed_tab:
        if not failed:
            st.success("No failed processing cases.")
        else:
            selected_failed = st.selectbox(
                "Choose a failed case",
                failed,
                format_func=lambda email_id: (
                    f"{email_id} · {email_by_id[email_id].get('subject', 'Untitled')}"
                ),
                key="failed_review_case",
            )
            failed_detail = artifacts.internal_results[selected_failed]
            st.caption(
                f"Attempts: {failed_detail.get('processing_attempts', 1)} · "
                f"Retries: {failed_detail.get('retry_count', 0)}"
            )
            render_error_history(failed_detail)
            if st.button(
                "Retry failed processing",
                type="primary",
                width="stretch",
                key=f"retry_{selected_failed}",
            ):
                try:
                    updated = retry_failed_email(
                        dataset_bundle, artifacts, selected_failed
                    )
                    st.session_state.dataset_artifacts = updated
                    st.session_state.submission_ready = False
                    st.session_state.pop("export_files", None)
                    write_artifacts(updated, DEFAULT_OUTPUT)
                    st.session_state.retry_notice = True
                    st.rerun()
                except (OSError, KeyError, ValueError, RuntimeError) as exc:
                    st.error(str(exc))

    with resolved_tab:
        if not resolved:
            st.info("No human review decisions have been saved yet.")
        else:
            rows = []
            for email_id in resolved:
                review = artifacts.internal_results[email_id]["human_review"]
                rows.append(
                    {
                        "email_id": email_id,
                        "subject": email_by_id[email_id].get("subject", ""),
                        "decision": review.get("decision", ""),
                        "reviewed_at": review.get("reviewed_at", ""),
                        "result": artifacts.submission[email_id]["status"],
                        "note": review.get("note", ""),
                    }
                )
            st.dataframe(rows, hide_index=True, width="stretch")


with st.sidebar:
    st.markdown(
        '<div class="sidebar-brand"><div class="brand-mark">C</div><div><div class="brand-name">CargoCheck</div><div class="brand-sub">Document verification</div></div></div><div class="sidebar-caption">Workspace</div>',
        unsafe_allow_html=True,
    )
    if "active_page" not in st.session_state:
        st.session_state.active_page = "Home"
    nav_items = [
        ("Home", "Dashboard"),
        ("Check one case", "Verify one request"),
        ("Analyze inbox", "Inbox operations"),
        ("Human review", "Human review"),
        ("Export results", "Submission center"),
    ]
    for destination, label in nav_items:
        st.button(
            label,
            key=f"nav_{destination}",
            type="primary" if st.session_state.active_page == destination else "secondary",
            width="stretch",
            on_click=lambda target=destination: st.session_state.update(active_page=target),
        )
    page = st.session_state.active_page

page_titles = {
    "Home": ("Operations Workspace", "Monitor document checks and start your next task."),
    "Check one case": ("Verify One Request", "Check an email, Shipping Instruction and draft Bill of Lading."),
    "Analyze inbox": ("Inbox Operations", "Turn incoming shipping emails into a prioritized action queue."),
    "Human review": ("Human Review", "Confirm uncertain values, correct extraction results, and retry failed cases."),
    "Export results": ("Submission Center", "Validate and download the final verification results."),
}
banner_title, banner_copy = page_titles[page]
st.markdown(
    f'<div class="page-banner"><div><div class="page-banner-title">{banner_title}</div><div class="page-banner-copy">{banner_copy}</div></div></div>',
    unsafe_allow_html=True,
)
bundle_path = str(DEFAULT_BUNDLE)


def go_to(destination: str) -> None:
    st.session_state.active_page = destination


def reset_single_case() -> None:
    for key in ("single_artifacts", "single_email", "single_si", "single_bl"):
        st.session_state.pop(key, None)


if page == "Home":
    st.markdown(
        """
        <div class="page-intro">
            <div class="eyebrow">Dashboard</div>
            <div class="page-title">Shipping verification workspace</div>
            <div class="page-copy">Review document exceptions, verify shipment records, and resolve issues.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "dataset_artifacts" in st.session_state:
        summary = st.session_state.dataset_artifacts.summary
        mismatch = summary.get("mismatch",0)
        review = summary.get("manual_review",0)
        passed = summary.get("no_mismatch",0)
        emails = summary.get("emails_processed",0)
        checks = summary.get("comparison_requests",0)
    else:
        mismatch = review = passed = emails = checks = 0

    st.markdown("### Cases requiring attention")
    cols=st.columns(3)
    cards=[("Mismatch",mismatch,"Correct BL values"),("Human review",review,"Needs verification"),("Completed",passed,"No action required")]
    for c,(t,n,d) in zip(cols,cards):
        with c:
            st.markdown(f"""<div class="alert-card"><div class="dashboard-label">{t}</div><div class="dashboard-number">{n}</div><div>{d}</div></div>""",unsafe_allow_html=True)

    st.markdown("### Quick actions")
    a,b=st.columns(2)
    with a: st.button("Verify one request",type="primary",width="stretch",on_click=go_to,args=("Check one case",))
    with b: st.button("Review inbox",width="stretch",on_click=go_to,args=("Analyze inbox",))

    st.markdown("### Today's overview")
    c=st.columns(3)
    for col,(l,v) in zip(c,[("Emails processed",emails),("Document checks",checks),("Passed automatically",passed)]):
        with col: st.metric(l,v)


elif page == "Check one case":
    if "single_artifacts" not in st.session_state:
        st.markdown(
            '<div class="page-intro"><div class="page-title">Check one shipping request</div><div class="page-copy">Add the three files below. After verification, this form will be replaced by a clear analysis result.</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="step-card"><div class="step-head"><div class="step-badge">1</div><div class="step-title">Add the email request</div></div><div class="step-help">Choose the email JSON from the participant dataset.</div></div>', unsafe_allow_html=True)
        email: dict[str, Any] | None = None
        email_file = st.file_uploader("Choose email JSON", type=["json"], key="single_email", help="This file contains the sender, subject, message, and email ID.")
        if email_file is not None:
            try:
                email = load_email_json(email_file)
                st.success(f"Email loaded: {email.get('subject', 'Untitled email')}")
            except ValueError as exc:
                st.error(str(exc))

        st.markdown('<div class="step-card"><div class="step-head"><div class="step-badge">2</div><div class="step-title">Add the two shipping documents</div></div><div class="step-help">SI is the approved instruction. BL is the carrier draft that must be checked against it.</div></div>', unsafe_allow_html=True)
        upload_cols = st.columns(2)
        si_file = upload_cols[0].file_uploader("Shipping Instruction (SI)", type=["txt", "pdf", "docx", "xlsx"], key="single_si", help="The reference document containing the intended shipment details.")
        bl_file = upload_cols[1].file_uploader("Draft Bill of Lading (BL)", type=["txt", "pdf", "docx", "xlsx"], key="single_bl", help="The draft document CargoCheck will verify against the SI.")

        st.markdown('<div class="step-card"><div class="step-head"><div class="step-badge">3</div><div class="step-title">Run the verification</div></div><div class="step-help">You will receive a field-by-field result, confidence level, and source evidence.</div></div>', unsafe_allow_html=True)
        if st.button("Verify documents", type="primary", width="stretch", key="process_single"):
            if email is None:
                st.error("Complete step 1 by uploading an email JSON.")
            elif not str(email.get("subject", "")).strip():
                st.error("Enter an email subject so CargoCheck can classify the request.")
            else:
                try:
                    with st.spinner("Classifying the email and checking the documents..."):
                        st.session_state.single_artifacts = process_single_email(email, uploaded_attachment(si_file), uploaded_attachment(bl_file))
                    st.rerun()
                except (OSError, ValueError) as exc:
                    st.error(str(exc))
    else:
        st.markdown('<div class="result-mode-head"><div><div class="eyebrow">Analysis complete</div><div class="result-mode-title">Verification result</div><div class="result-mode-copy">The upload form is hidden while you review this result.</div></div></div>', unsafe_allow_html=True)
        render_single_result(st.session_state.single_artifacts)
        st.button("Analyze another case", type="primary", width="stretch", on_click=reset_single_case, key="reset_single")

elif page == "Analyze inbox":
    st.markdown('<div class="page-intro"><div class="page-title">Inbox operations</div><div class="page-copy">Turn a mixed inbox into a prioritized work queue. CargoCheck separates routine messages, finds document discrepancies, and brings uncertain cases to the top.</div></div>', unsafe_allow_html=True)
    if "dataset_artifacts" not in st.session_state:
        st.markdown('<div class="journey"><div class="journey-step"><div class="journey-dot">1</div><div class="journey-title">Sort the inbox</div><div class="journey-copy">CargoCheck identifies which emails actually need document checking.</div></div><div class="journey-step"><div class="journey-dot">2</div><div class="journey-title">Check SI against BL</div><div class="journey-copy">The seven shipment fields are compared only for the relevant requests.</div></div><div class="journey-step"><div class="journey-dot">3</div><div class="journey-title">See your next tasks</div><div class="journey-copy">Corrections and uncertain cases appear first in one simple queue.</div></div></div>', unsafe_allow_html=True)
        st.write("")
    if st.button("Analyze inbox and build work queue", type="primary", width="stretch", key="run_dataset"):
        try:
            artifacts = run_dataset_with_progress(bundle_path)
            st.session_state.dataset_artifacts = artifacts
            st.session_state.dataset_bundle = str(Path(bundle_path).expanduser().resolve())
            st.session_state.submission_ready = False
            st.session_state.pop("export_files", None)
            write_artifacts(artifacts, DEFAULT_OUTPUT)
            st.success("Inbox analysis completed successfully.")
        except (OSError, ValueError, RuntimeError) as exc:
            st.error(str(exc))
    if "dataset_artifacts" in st.session_state:
        render_summary(st.session_state.dataset_artifacts)
        render_inbox_workspace(st.session_state.dataset_artifacts)

elif page == "Human review":
    st.markdown(
        '<div class="page-intro"><div class="page-title">Human review dashboard</div><div class="page-copy">Resolve uncertain cases by confirming or correcting all seven extracted fields. Processing failures keep their complete error history and can be retried individually.</div></div>',
        unsafe_allow_html=True,
    )
    artifacts = st.session_state.get("dataset_artifacts")
    if artifacts is None:
        st.warning(
            "No inbox results yet. Open **Inbox operations** and run the analysis first."
        )
    else:
        render_human_review_dashboard(
            artifacts,
            st.session_state.get("dataset_bundle", bundle_path),
        )

else:
    st.markdown('<div class="page-intro"><div class="page-title">Report and export</div><div class="page-copy">Review the comparison results, download operational reports in JSON, CSV, or PDF, and validate the official submission file.</div></div>', unsafe_allow_html=True)
    artifacts = st.session_state.get("dataset_artifacts")
    if artifacts is None:
        st.warning("No inbox results yet. Open **Analyze inbox** from the left menu and run the analysis first.")
    else:
        render_summary(artifacts)
        section_label("Comparison results")
        render_dataset_table(artifacts)

        section_label("Operational report exports")
        st.caption(
            "These reports include comparison values, processing errors, retries, and human review decisions."
        )
        if st.button(
            "Prepare JSON, CSV and PDF reports",
            type="primary",
            width="stretch",
            key="prepare_reports",
        ):
            try:
                with st.spinner("Building report files..."):
                    exports = {
                        "json": results_json_bytes(artifacts),
                        "csv": results_csv_bytes(artifacts),
                    }
                    try:
                        exports["pdf"] = results_pdf_bytes(artifacts)
                    except PdfExportUnavailable as exc:
                        st.warning(str(exc))
                    st.session_state.export_files = exports
                if "pdf" in exports:
                    st.success("JSON, CSV, and PDF reports are ready to download.")
                else:
                    st.success("JSON and CSV reports are ready to download.")
            except Exception as exc:
                st.session_state.pop("export_files", None)
                st.error(f"Report generation failed: {exc}")
        if st.session_state.get("export_files"):
            exports = st.session_state.export_files
            download_columns = st.columns(3 if "pdf" in exports else 2)
            download_columns[0].download_button(
                "Download results.json",
                data=exports["json"],
                file_name="verification-results.json",
                mime="application/json",
                width="stretch",
            )
            download_columns[1].download_button(
                "Download results.csv",
                data=exports["csv"],
                file_name="verification-results.csv",
                mime="text/csv",
                width="stretch",
            )
            if "pdf" in exports:
                download_columns[2].download_button(
                    "Download report.pdf",
                    data=exports["pdf"],
                    file_name="verification-report.pdf",
                    mime="application/pdf",
                    width="stretch",
                )

        section_label("Official submission")
        if st.button("Validate submission", type="primary", width="stretch", key="generate_submission"):
            try:
                resolved_bundle = str(Path(bundle_path).expanduser().resolve())
                inbox_sample = json.loads((Path(resolved_bundle) / "sample_submission.json").read_text())
                validate_submission(artifacts.submission, inbox_sample)
                write_artifacts(artifacts, DEFAULT_OUTPUT)
                st.session_state.submission_ready = True
                st.success("Ready to submit — the schema is valid and every email is present.")
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
                st.session_state.submission_ready = False
                st.error(str(exc))
        if st.session_state.get("submission_ready"):
            st.download_button("Download submission.json", data=submission_bytes(artifacts.submission), file_name="submission.json", mime="application/json", type="primary", width="stretch")
