"""Visualization tools exposed to the LLM."""
import json, uuid
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from langchain_core.tools import tool
from ..config import PLOTS_DIR
from ..utils import load_df

@tool
def time_plot(path: str) -> str:
    """Plot first 4 numeric columns over time; return JSON with image path."""
    df = load_df(path)
    img = PLOTS_DIR / f"time_{uuid.uuid4().hex}.png"
    df.iloc[:, :4].plot(figsize=(6, 4), title="Time‑domain")
    plt.tight_layout()
    plt.savefig(img)
    plt.close()
    return json.dumps({"image_path": str(img)})

@tool
def freq_plot(path: str) -> str:
    """Simple FFT plot for first 4 columns; return JSON with image path."""
    df = load_df(path)
    N = len(df)
    T = 1.0
    xf = np.fft.rfftfreq(N, T)
    img = PLOTS_DIR / f"freq_{uuid.uuid4().hex}.png"
    plt.figure(figsize=(6, 4))
    for col in df.columns[:4]:
        yf = np.fft.rfft(df[col].values)
        plt.plot(xf, np.abs(yf), label=str(col))
    plt.title("Frequency Spectrum")
    plt.legend()
    plt.tight_layout()
    plt.savefig(img)
    plt.close()
    return json.dumps({"image_path": str(img)})
