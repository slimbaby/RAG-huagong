"""
RAGAS 子进程评分脚本

在独立 Python 进程中运行 RAGAS，避免 uvloop/nest_asyncio 冲突。

输入 (stdin JSON):
  question, answer, contexts, ground_truth, api_key, base_url, model

输出 (stdout JSON):
  {"faithfulness": 0.8, "context_precision": 0.9, "context_recall": 0.6}
"""

import sys
import json
import os

# Prevent torch/sentence-transformers from loading heavy models
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"


class DummyEmbeddings:
    """Lightweight embedding stub that avoids loading torch/HuggingFace models."""

    def embed_query(self, text):
        return [0.0] * 768

    def embed_documents(self, texts):
        return [[0.0] * 768 for _ in texts]

    async def aembed_query(self, text):
        return [0.0] * 768

    async def aembed_documents(self, texts):
        return [[0.0] * 768 for _ in texts]


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

    dataset = Dataset.from_dict({
        "question": [input_data["question"]],
        "answer": [input_data["answer"]],
        "contexts": [input_data["contexts"]],
        "ground_truth": [input_data["ground_truth"] or input_data["answer"]],
    })

    llm = LangchainLLMWrapper(ChatOpenAI(
        model=input_data["model"],
        openai_api_key=input_data["api_key"],
        openai_api_base=input_data["base_url"],
        temperature=0.1,
    ))

    # Use dummy embeddings to prevent torch/HuggingFace model loading (which causes segfault)
    embeddings = LangchainEmbeddingsWrapper(DummyEmbeddings())

    metrics = [
        Faithfulness(llm=llm),
        ContextPrecision(llm=llm),
        ContextRecall(llm=llm),
    ]

    result = evaluate(dataset, metrics=metrics, embeddings=embeddings)

    scores = {}
    for m in ["faithfulness", "context_precision", "context_recall"]:
        try:
            val = result[m]
            scores[m] = round(float(val), 4) if val is not None else 0.0
        except (KeyError, TypeError, IndexError):
            scores[m] = 0.0

    json.dump(scores, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        json.dump({
            "error": str(e),
            "faithfulness": 0,
            "context_precision": 0,
            "context_recall": 0,
        }, sys.stdout)
        sys.exit(1)
