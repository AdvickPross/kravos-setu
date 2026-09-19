#!/usr/bin/env python3
"""Runtime Diagnostics CLI for Kravos Setu OCR & Document Intelligence Pipeline."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.loader import PolicyConfig
from models.manager import ModelManager


def collect_system_diagnostics(profile_name: str | None = None, device: str | None = None) -> dict[str, Any]:
    """Gather complete hardware, runtime, ML framework, and profile diagnostics."""
    diagnostics: dict[str, Any] = {}

    # 1. Host & OS
    diagnostics["system"] = {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "cpu_count_logical": os.cpu_count(),
    }

    # 2. PyTorch & CUDA
    pytorch_info: dict[str, Any] = {"installed": False}
    try:
        import torch

        pytorch_info["installed"] = True
        pytorch_info["version"] = torch.__version__
        cuda_available = torch.cuda.is_available()
        pytorch_info["cuda_available"] = cuda_available
        pytorch_info["cuda_device_count"] = torch.cuda.device_count() if cuda_available else 0

        if cuda_available:
            pytorch_info["cuda_version"] = torch.version.cuda
            devices = []
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                devices.append({
                    "index": i,
                    "name": props.name,
                    "total_memory_gb": round(props.total_memory / (1024**3), 2),
                    "multi_processor_count": props.multi_processor_count,
                    "compute_capability": f"{props.major}.{props.minor}",
                })
            pytorch_info["devices"] = devices
    except ImportError:
        pytorch_info["installed"] = False
    diagnostics["pytorch"] = pytorch_info

    # 3. ONNX Runtime
    onnx_info: dict[str, Any] = {"installed": False}
    try:
        import onnxruntime as ort

        onnx_info["installed"] = True
        onnx_info["version"] = ort.__version__
        onnx_info["available_providers"] = ort.get_available_providers()
    except ImportError:
        onnx_info["installed"] = False
    diagnostics["onnxruntime"] = onnx_info

    # 4. OpenCV & Threading
    cv_info: dict[str, Any] = {"installed": False}
    try:
        import cv2

        cv_info["installed"] = True
        cv_info["version"] = cv2.__version__
        cv_info["num_threads"] = cv2.getNumThreads()
    except ImportError:
        cv_info["installed"] = False
    diagnostics["opencv"] = cv_info

    # 5. Kravos Setu Profile & Configuration
    policy = PolicyConfig.load(profile=profile_name)
    manager = ModelManager(policy=policy.memory, device=device)

    diagnostics["kravos_configuration"] = {
        "active_profile": policy.default_profile,
        "resolved_device": manager.device,
        "configured_device_request": manager.policy.device_preference,
        "resolved_precision": manager.precision,
        "configured_precision_request": manager.policy.precision,
        "max_resident_models": manager.policy.max_resident_models,
        "eviction_enabled": manager.policy.enable_eviction,
        "idle_timeout_seconds": manager.policy.idle_timeout_seconds,
        "configured_cpu_threads": manager.configured_threads,
        "pdf_dpi": policy.pdf_dpi,
    }

    # 6. Memory Status (RSS + VRAM)
    diagnostics["memory_status"] = manager.get_memory_status()

    # 7. Model Adapters Metadata
    diagnostics["models"] = manager.get_all_metadata()

    return diagnostics


def print_formatted_diagnostics(d: dict[str, Any]) -> None:
    """Print clean terminal diagnostic summary."""
    sys_info = d["system"]
    cfg = d["kravos_configuration"]
    pt = d["pytorch"]
    ort = d["onnxruntime"]
    mem = d["memory_status"]

    print("=" * 70)
    print("        KRAVOS SETU - RUNTIME ENVIRONMENT & PIPELINE DIAGNOSTICS")
    print("=" * 70)

    print(f"\n[HOST ENVIRONMENT]")
    print(f"  OS:                 {sys_info['os']} {sys_info['os_release']} ({sys_info['machine']})")
    print(f"  Python:             {sys_info['python_version']} ({sys_info['python_executable']})")
    print(f"  CPU Cores:          {sys_info['cpu_count_logical']} logical")

    print(f"\n[HARDWARE ACCELERATION & FRAMEWORKS]")
    if pt["installed"]:
        cuda_status = "Available" if pt["cuda_available"] else "Not Available (CPU mode active)"
        print(f"  PyTorch:            v{pt['version']} (CUDA: {cuda_status})")
        if pt["cuda_available"]:
            for dev in pt.get("devices", []):
                print(f"    - GPU {dev['index']}: {dev['name']} ({dev['total_memory_gb']} GB VRAM, CC {dev['compute_capability']})")
    else:
        print("  PyTorch:            Not Installed")

    if ort["installed"]:
        print(f"  ONNX Runtime:       v{ort['version']}")
        print(f"  ONNX Providers:     {', '.join(ort['available_providers'])}")
    else:
        print("  ONNX Runtime:       Not Installed")

    cv = d.get("opencv", {})
    if cv.get("installed"):
        print(f"  OpenCV Threads:     {cv.get('num_threads')} (v{cv.get('version')})")

    print(f"\n[KRAVOS PIPELINE PROFILE: '{cfg['active_profile']}']")
    print(f"  Resolved Device:    {cfg['resolved_device'].upper()} (request: {cfg['configured_device_request']})")
    print(f"  Resolved Precision: {cfg['resolved_precision'].upper()} (request: {cfg['configured_precision_request']})")
    print(f"  Model Residency:    Max {cfg['max_resident_models']} models (LRU Eviction: {'Enabled' if cfg['eviction_enabled'] else 'Disabled - Fully Resident'})")
    print(f"  CPU Threads:        {cfg['configured_cpu_threads']}")
    print(f"  PDF Raster DPI:     {cfg['pdf_dpi']}")

    print(f"\n[PROCESS MEMORY STATUS]")
    rss_mb = mem.get("process_rss_mb")
    rss_str = f"{rss_mb:.1f} MB" if rss_mb is not None else "N/A"
    print(f"  Process RSS:        {rss_str}")
    vram = mem.get("cuda_vram")
    if vram:
        print(f"  CUDA VRAM Total:    {vram.get('total_mb', 0):.1f} MB")
        print(f"  CUDA VRAM Alloc:    {vram.get('allocated_mb', 0):.1f} MB")
        print(f"  CUDA VRAM Peak:     {vram.get('peak_allocated_mb', 0):.1f} MB")
    else:
        print(f"  CUDA VRAM:          N/A (CPU execution)")

    print(f"\n[REGISTERED MODEL ADAPTERS]")
    for model_name, meta in d.get("models", {}).items():
        loaded_str = "Loaded" if meta.get("weights_loaded") else "Cold / Unloaded"
        backend = meta.get("backend", "unknown")
        device = meta.get("execution_device", "cpu")
        precision = meta.get("precision", "fp32")
        print(f"  * {model_name:<12} | Status: {loaded_str:<15} | Backend: {backend:<30} | Device: {device}:{precision}")

    print("=" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description="Kravos Setu OCR & Document Intelligence System Diagnostics")
    parser.add_argument("--profile", "-p", help="Hardware profile name (e.g. laptop, jetson)", default=None)
    parser.add_argument("--device", "-d", help="Execution device override (e.g. auto, cpu, cuda)", default=None)
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()

    diagnostics = collect_system_diagnostics(profile_name=args.profile, device=args.device)

    if args.json:
        print(json.dumps(diagnostics, indent=2))
    else:
        print_formatted_diagnostics(diagnostics)

    return 0


if __name__ == "__main__":
    sys.exit(main())
