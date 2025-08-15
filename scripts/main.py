# scripts/main.py  ── 修改后版本
import argparse, logging, pathlib, sys, importlib

# ① 把项目根（dynamic_langgraph_project）塞进 sys.path，确保包可见
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

# ② 再安全导入
run_dynamic_pipeline = importlib.import_module(
    "dynamic_langgraph.pipeline"
).run_dynamic_pipeline

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def main():
    parser = argparse.ArgumentParser(description="Run dynamic LangGraph pipeline.")
    parser.add_argument("txt", help="Path to vibration TXT/CSV file")
    parser.add_argument("-q", "--query", required=True, help="User query / requirement")
    args = parser.parse_args()

    result = run_dynamic_pipeline(pathlib.Path(args.txt), args.query)
    #print("\n LLM 规划任务流:", tasks)
    print(" 摘要:\n", result["summary"]) # asr_text summary

if __name__ == "__main__":
    main()
