from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Iterable

from langchain_core.embeddings import Embeddings


TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")


class HashEmbeddings(Embeddings):
    """Deterministic local embeddings for demo and offline validation."""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for token, weight in self._features(text).items():
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1 if digest[4] % 2 == 0 else -1
            vec[idx] += sign * weight
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _features(self, text: str) -> Counter[str]:
        tokens = TOKEN_RE.findall(text.lower())
        features: Counter[str] = Counter(tokens)
        for left, right in zip(tokens, tokens[1:]):
            features[f"{left}_{right}"] += 1
        return features


def local_answer(question: str, context: str, sources: Iterable[str]) -> str:
    sentences = split_sentences(context)
    q_tokens = set(TOKEN_RE.findall(question.lower()))
    ranked = []
    for sentence in sentences:
        s_tokens = set(TOKEN_RE.findall(sentence.lower()))
        overlap = len(q_tokens & s_tokens)
        if overlap:
            ranked.append((overlap, len(sentence), sentence))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = [item[2] for item in ranked[:4]] or sentences[:3]
    basis = "\n".join(f"- {line}" for line in selected if line.strip())
    source_text = "、".join(sources)
    return (
        "直接回答：\n"
        f"{' '.join(selected).strip()}\n\n"
        "依据说明：\n"
        f"以上结论来自检索到的知识库片段，关联来源为：{source_text}。\n\n"
        "来源：\n"
        f"{basis}\n\n"
        "不确定性：\n"
        "如原文未覆盖具体金额、日期或审批角色，请以来源文档为准并补充资料。"
    )


def local_material(material_type: str, topic: str, context: str, source_labels: list[str]) -> str:
    sentences = [s for s in split_sentences(context) if s.strip()]
    selected = sentences[:12]
    sources = "、".join(source_labels)
    if material_type == "政策摘要":
        bullets = "\n".join(f"- {sentence}" for sentence in selected[:8]) or "- 资料未说明。"
        return (
            f"# {topic}政策摘要\n\n"
            f"## 关键规则\n\n{bullets}\n\n"
            "## 适用对象\n\n"
            "以来源文档明确说明为准；资料未说明的对象不得推断。\n\n"
            "## 注意事项\n\n"
            "- 涉及金额、日期、审批人的内容必须能在来源中找到。\n"
            "- 资料缺失时需要补充制度或流程文档。\n\n"
            f"## 来源\n\n{sources}"
        )
    if material_type == "邮件草稿":
        summary = " ".join(selected[:4]) or "资料未说明。"
        return (
            f"# {topic}邮件草稿\n\n"
            "收件人：资料未说明\n\n"
            f"主题：关于{topic}的说明\n\n"
            "正文：\n\n"
            "您好，\n\n"
            f"根据当前知识库资料，{summary}\n\n"
            "请您结合实际业务场景确认。如需进一步处理，请补充相关制度、审批记录或业务说明。\n\n"
            "谢谢。\n\n"
            f"来源：{sources}"
        )
    if material_type == "FAQ":
        questions = [
            f"{topic}适用于哪些情况？",
            f"{topic}需要准备哪些材料？",
            f"{topic}的审批或处理节点是什么？",
            f"{topic}有哪些注意事项？",
            f"{topic}资料未说明的内容有哪些？",
        ]
        blocks = []
        for idx, question in enumerate(questions, start=1):
            answer = selected[idx - 1] if idx - 1 < len(selected) else "资料未说明。"
            blocks.append(f"### Q{idx}. {question}\n\n{answer}\n\n来源：{sources}")
        return f"# {topic} FAQ\n\n" + "\n\n".join(blocks)

    steps = []
    for idx, sentence in enumerate(selected[:6], start=1):
        steps.append(
            f"| {idx} | {sentence} | 资料未说明时需人工确认 | 见来源 | 关注资料完整性 |"
        )
    table = "\n".join(steps) or "| 1 | 资料未说明 | 资料未说明 | 资料未说明 | 补充制度文档 |"
    return (
        f"# {topic}流程清单\n\n"
        f"适用范围：基于当前知识库中与“{topic}”相关的资料整理。\n\n"
        "| 序号 | 步骤 | 负责人 | 所需材料/审批节点 | 风险提醒 |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"{table}\n\n"
        f"来源：{sources}\n\n"
        "备注：资料未说明的字段不得推断，演示或实际使用前需要业务负责人确认。"
    )


def split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[。！？；.!?])\s+|(?<=\.)\s+", normalized)
    return [part.strip() for part in parts if part.strip()]
