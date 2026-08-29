"""
RAGAS 子进程评分脚本（批量模式）

在独立 Python 进程中运行 RAGAS，与主服务进程完全隔离：
主进程（uvicorn + paddle/torch 等）直接 import ragas 会因运行时冲突 segfault，
本脚本通过 stdin/stdout JSON 通信，崩溃/超时只影响本次评分，不波及主服务。

输入 (stdin JSON):
  {
    "samples": [{"question", "answer", "contexts", "ground_truth"}, ...],
    "api_key", "base_url", "model"
  }

输出 (stdout JSON):
  {"scores": [{"faithfulness": 0.8, "context_precision": 0.9, "context_recall": 0.6}, ...]}
"""

import sys
import json
import os

# 阻止 ragas 间接加载 torch/sentence-transformers 模型（历史 segfault 根因之一）
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

METRIC_NAMES = ["faithfulness", "context_precision", "context_recall"]


class DummyEmbeddings:
    """轻量 embedding 桩，避免加载 torch/HuggingFace 模型。
    所选三个指标均为纯 LLM 指标，不依赖真实向量。"""

    def embed_query(self, text):
        return [0.0] * 768

    def embed_documents(self, texts):
        return [[0.0] * 768 for _ in texts]

    async def aembed_query(self, text):
        return [0.0] * 768

    async def aembed_documents(self, texts):
        return [[0.0] * 768 for _ in texts]


def zero_scores():
    return {m: 0.0 for m in METRIC_NAMES}


def main():
    input_data = json.loads(sys.stdin.read())

    os.environ["OPENAI_API_KEY"] = input_data["api_key"]
    os.environ["OPENAI_BASE_URL"] = input_data["base_url"]

    from ragas import evaluate
    from datasets import Dataset
    from ragas.llms import LangchainLLMWrapper
    from langchain_openai import ChatOpenAI
    from ragas.metrics import Faithfulness, ContextPrecision, ContextRecall
    from ragas.embeddings import LangchainEmbeddingsWrapper

    samples = input_data["samples"]

    dataset = Dataset.from_dict({
        "question": [s["question"] for s in samples],
        "answer": [s["answer"] for s in samples],
        "contexts": [s["contexts"] for s in samples],
        "ground_truth": [s.get("ground_truth") or s["answer"] for s in samples],
    })

    llm = LangchainLLMWrapper(ChatOpenAI(
        model=input_data["model"],
        openai_api_key=input_data["api_key"],
        openai_api_base=input_data["base_url"],
        temperature=0.1,
    ))

    # 用桩 embedding 阻止 torch/HuggingFace 模型加载（segfault 根因）
    embeddings = LangchainEmbeddingsWrapper(DummyEmbeddings())

    metrics = [
        Faithfulness(llm=llm),
        ContextPrecision(llm=llm),
        ContextRecall(llm=llm),
    ]

    result = evaluate(dataset, metrics=metrics, embeddings=embeddings)

    scores = []
    for i in range(len(samples)):
        sample_scores = zero_scores()
        for m in METRIC_NAMES:
            try:
                # ragas 对单样本失败时 result[m] 可能是 NaN 或含 None 的列表
                val = result[m]
                if isinstance(val, float):
                    v = val
                else:
                    v = val[i] if i < len(val) else None
                sample_scores[m] = round(float(v), 4) if v is not None else 0.0
            except (KeyError, TypeError, IndexError, ValueError):
                sample_scores[m] = 0.0
        scores.append(sample_scores)

    json.dump({"scores": scores}, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        json.dump({"error": str(e), "scores": [zero_scores()]}, sys.stdout)
        sys.exit(1)
