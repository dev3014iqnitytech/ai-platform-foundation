"""
Dealer Quote Generator - Streamlit Frontend Application.

An interactive web UI for uploading dealer PDF templates, triggering the
v2 zero-shot demo quote generation API, and viewing/downloading the rendered PDF.
"""

from __future__ import annotations

import base64
import os
import requests
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Constants
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BACKEND_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
DEMO_QUOTE_ENDPOINT = "/v2/demo-quote"
MAX_FILE_SIZE_MB = 5
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# ─────────────────────────────────────────────────────────────────────────────
# Page Configuration & Styling
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Dealer Quote Generator",
    page_icon="🚜",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom styling for clean, professional layout
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.1rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #64748B;
        margin-bottom: 1.5rem;
    }
    .status-card {
        padding: 1rem;
        border-radius: 8px;
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        margin-bottom: 1rem;
    }
    .pdf-preview-container {
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    }
    iframe {
        border-radius: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────────────────
# Session State Initialization
# ─────────────────────────────────────────────────────────────────────────────

if "generated_pdf_bytes" not in st.session_state:
    st.session_state["generated_pdf_bytes"] = None

if "last_processed_file_id" not in st.session_state:
    st.session_state["last_processed_file_id"] = None

if "output_filename" not in st.session_state:
    st.session_state["output_filename"] = "generated_quote.pdf"


def render_pdf_iframe(pdf_bytes: bytes, height: int = 750) -> None:
    """
    Encodes raw PDF bytes as base64 and renders directly inside an HTML iframe.
    """
    b64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
    iframe_html = f"""
        <div class="pdf-preview-container">
            <iframe
                src="data:application/pdf;base64,{b64_pdf}#toolbar=1&navpanes=0&scrollbar=1"
                width="100%"
                height="{height}"
                type="application/pdf"
                style="border: 1px solid #CBD5E1; border-radius: 8px;"
            >
                <p>Your browser does not support inline PDF viewing. Please use the download button below.</p>
            </iframe>
        </div>
    """
    st.markdown(iframe_html, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar: Backend Connection & Info
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image(
        "https://img.icons8.com/fluency/96/tractor.png",
        width=72,
    )
    st.title("Settings & Status")

    backend_base_url = st.text_input(
        "API Base URL",
        value=DEFAULT_BACKEND_URL,
        help="Base host URL of the FastAPI backend service",
    ).rstrip("/")

    api_url = f"{backend_base_url}{DEMO_QUOTE_ENDPOINT}"

    st.markdown("---")
    st.markdown("### 📌 Endpoint Information")
    st.caption(f"**Target Endpoint:** `{DEMO_QUOTE_ENDPOINT}`")
    st.caption(f"**Full URL:** `{api_url}`")
    st.caption("**Method:** `POST (multipart/form-data)`")

    st.markdown("---")
    st.markdown("### 💡 How It Works")
    st.markdown(
        """
        1. **Upload** your dealer PDF template.
        2. **Click** the enabled *Generate PDF* button.
        3. **FastAPI (`v2/demo-quote`)** extracts coordinates and dynamic template structures.
        4. **Preview & Download** the freshly rendered quote PDF instantly.
        """
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main Application Content
# ─────────────────────────────────────────────────────────────────────────────

st.markdown('<div class="main-header">🚜 Dealer Quote Generator</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">Zero-shot dynamic template population and quote PDF rendering engine</div>',
    unsafe_allow_html=True,
)

col_upload, col_action = st.columns([2, 1], gap="medium")

with col_upload:
    uploaded_file = st.file_uploader(
        label="📄 Upload Dealer PDF Template (Max 5MB)",
        type=["pdf"],
        accept_multiple_files=False,
        help="Upload a single PDF template file (maximum 5MB) to populate with dealer quote data.",
    )

# Determine button activation based on whether a valid file is uploaded
is_file_ready = uploaded_file is not None
file_size_error = False

if is_file_ready and uploaded_file.size > MAX_FILE_SIZE_BYTES:
    is_file_ready = False
    file_size_error = True
    st.error(
        f"❌ File size exceeds the maximum limit of {MAX_FILE_SIZE_MB}MB "
        f"({uploaded_file.size / (1024 * 1024):.2f}MB). Please upload a file up to {MAX_FILE_SIZE_MB}MB."
    )

# Handle file change/reset: if user removes file or changes it, reset generated PDF state
if not is_file_ready:
    st.session_state["generated_pdf_bytes"] = None
    st.session_state["last_processed_file_id"] = None
elif st.session_state["last_processed_file_id"] != uploaded_file.file_id:
    # A new file has been uploaded; clear previous generated output
    st.session_state["generated_pdf_bytes"] = None
    st.session_state["last_processed_file_id"] = uploaded_file.file_id

with col_action:
    st.write("")
    st.write("")
    st.markdown("**Action**")

    # Generate PDF button: strictly disabled if no file is uploaded
    generate_button = st.button(
        label="⚡ Generate PDF",
        type="primary",
        disabled=not is_file_ready,
        use_container_width=True,
        help="Enabled once a valid PDF file (<= 5MB) is uploaded above.",
    )

    if not is_file_ready:
        if file_size_error:
            st.error(f"⚠️ File exceeds {MAX_FILE_SIZE_MB}MB limit.")
        else:
            st.info("👆 Upload a PDF template to enable generation.")
    else:
        st.success(f"📎 Ready: **{uploaded_file.name}** ({uploaded_file.size / 1024:.1f} KB)")

# ─────────────────────────────────────────────────────────────────────────────
# API Request Handling & Execution
# ─────────────────────────────────────────────────────────────────────────────

if generate_button and is_file_ready:
    with st.spinner("Processing your quote and generating PDF..."):
        try:
            # Read file bytes from Streamlit uploaded file
            file_bytes = uploaded_file.getvalue()

            # Prepare multipart/form-data payload with the uploaded PDF
            files_payload = {
                "file": (
                    uploaded_file.name,
                    file_bytes,
                    "application/pdf",
                )
            }

            # Send POST request to the v2/demo-quote endpoint
            response = requests.post(
                url=api_url,
                files=files_payload,
                timeout=90,
            )

            # Check response status
            if response.status_code == 200:
                # Raw generated PDF bytes returned directly in the response
                generated_bytes = response.content

                # Determine suggested filename from response headers or fallback
                content_disp = response.headers.get("Content-Disposition", "")
                output_filename = "generated_quote.pdf"
                if "filename=" in content_disp:
                    filename_part = content_disp.split("filename=")[-1].strip('"\'')
                    if filename_part:
                        output_filename = filename_part
                else:
                    output_filename = f"quote_{uploaded_file.name}"

                # Store in session state for persistence across Streamlit reruns
                st.session_state["generated_pdf_bytes"] = generated_bytes
                st.session_state["output_filename"] = output_filename
                st.toast("🎉 PDF quote successfully generated!", icon="✅")

            else:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get("detail", response.text)
                except Exception:
                    pass

                st.error(
                    f"❌ **Backend returned HTTP {response.status_code}**: {error_detail}"
                )

        except requests.exceptions.ConnectionError:
            st.error(
                f"❌ **Connection Failed**: Could not reach `{api_url}`. "
                "Ensure the FastAPI server is running (e.g. `uvicorn app.main:app --port 8000`)."
            )
        except requests.exceptions.Timeout:
            st.error("⏱️ **Request Timeout**: The PDF generation took longer than 90 seconds.")
        except requests.exceptions.RequestException as req_err:
            st.error(f"❌ **Network Error**: {req_err}")
        except Exception as exc:
            st.error(f"❌ **Unexpected Error**: {exc}")

# ─────────────────────────────────────────────────────────────────────────────
# File Display & Downloading
# ─────────────────────────────────────────────────────────────────────────────

if st.session_state.get("generated_pdf_bytes") is not None:
    st.markdown("---")
    
    col_header, col_download = st.columns([2, 1], gap="medium")
    with col_header:
        st.subheader("📑 Generated PDF Document Preview")
        st.caption(f"Filename: `{st.session_state['output_filename']}`")

    with col_download:
        st.write("")
        st.download_button(
            label="📥 Download Generated PDF",
            data=st.session_state["generated_pdf_bytes"],
            file_name=st.session_state["output_filename"],
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )

    # Render PDF preview inline using iframe
    render_pdf_iframe(st.session_state["generated_pdf_bytes"])
