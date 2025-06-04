"""CNN‑based fault diagnosis tool exposed to the LLM."""
import json, torch, torch.nn as nn
from pathlib import Path
from langchain_core.tools import tool
from ..utils import load_df

class SimpleCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv1d(1, 6, 21, padding="same")
        self.bn1   = nn.BatchNorm1d(6)
        self.pool1 = nn.MaxPool1d(2)
        self.conv2 = nn.Conv1d(6, 10, 21, padding="same")
        self.pool2 = nn.MaxPool1d(2)
        self.conv3 = nn.Conv1d(10, 10, 15, padding="same")
        self.pool3 = nn.MaxPool1d(2)
        self.conv4 = nn.Conv1d(10, 10, 10, padding="same")
        self.pool4 = nn.MaxPool1d(2)
        self.dropout = nn.Dropout(0.25)
        self.flatten = nn.Flatten()
        self.fc1 = nn.Linear(10*75, 1024)
        self.fc2 = nn.Linear(1024, 4)
    def forward(self, x):
        x = x.permute(0, 2, 1)
        x = self.pool1(self.bn1(self.conv1(x)))
        x = self.pool2(self.conv2(x))
        x = self.pool3(self.conv3(x))
        x = self.pool4(self.conv4(x))
        x = self.dropout(x)
        x = self.flatten(x)
        x = torch.relu(self.fc1(x))
        x = self.dropout(x)
        return self.fc2(x)

MODEL_PATH = Path(__file__).resolve().parent.parent.parent / "simple_cnn.pth"

@tool
def diagnose_signal(path: str) -> str:
    """Diagnose vibration signal into 4 classes; returns JSON."""
    df = load_df(path)
    data = df.values.flatten()[:1200]
    if len(data) < 1200:
        return json.dumps({"error": "INPUT_TOO_SHORT"})
    model = SimpleCNN()
    model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
    model.eval()
    with torch.no_grad():
        out = model(torch.tensor(data, dtype=torch.float32).unsqueeze(0).unsqueeze(-1))
        label = ["轴承滚珠故障", "健康状态", "轴承内圈故障", "轴承外圈故障"][out.argmax().item()]
    return json.dumps({"prediction": label})
