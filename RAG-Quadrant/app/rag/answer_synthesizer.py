"""
Answer Synthesizer — Offline natural language answer generation.

Applying Single Responsibility Principle (SRP):
- Solely responsible for synthesizing human-readable answers from chunks.

Applying Dependency Inversion Principle (DIP):
- Implements IAnswerSynthesizer interface.
- All configuration (noise patterns, stop words) injected from constants.
"""

import re
import logging
from typing import List, Dict, Any

from app.core.interfaces import IAnswerSynthesizer
from app.rag.constants import (
    STOP_WORDS,
    NOISE_PATTERNS,
    MIN_SENTENCE_LENGTH,
    MIN_SENTENCE_WORDS,
    TOP_SENTENCES_PARAGRAPH_1,
    TOP_SENTENCES_PARAGRAPH_2_START,
    TOP_SENTENCES_PARAGRAPH_2_END,
    NO_DETAILED_INFO_RESPONSE,
)

logger = logging.getLogger(__name__)


class NaturalAnswerSynthesizer(IAnswerSynthesizer):
    """
    Synthesizes a fluid, coherent, human-readable narrative from retrieved chunks.

    Pipeline:
    1. Extract query tokens and model numbers for ranking
    2. Strip noise headers from chunk text
    3. Split into sentences, deduplicate, and clean
    4. Rank sentences by model-number match → query-keyword match → general
    5. Assemble top sentences into paragraph format
    """

    def __init__(
        self,
        stop_words: frozenset = STOP_WORDS,
        noise_patterns: list = None,
        min_sentence_length: int = MIN_SENTENCE_LENGTH,
        min_sentence_words: int = MIN_SENTENCE_WORDS,
    ):
        self._stop_words = stop_words
        self._noise_patterns = noise_patterns or NOISE_PATTERNS
        self._min_sentence_length = min_sentence_length
        self._min_sentence_words = min_sentence_words

    def synthesize(self, question: str, chunks: List[Dict[str, Any]]) -> str:
        """Synthesize a natural language answer from retrieved chunks."""
        q_tokens = self._extract_query_tokens(question)
        model_numbers = [tok for tok in q_tokens if tok.isdigit() or re.match(r'^\d+[a-z]*$', tok)]

        scored_sentences = []
        seen_normalized = set()

        for chunk in chunks:
            text = chunk.get("text", "")
            text = self._strip_noise(text)
            sentences = self._extract_sentences(text)

            for sentence in sentences:
                norm = sentence.lower()
                if norm in seen_normalized:
                    continue
                seen_normalized.add(norm)

                sentence = self._clean_sentence(sentence)

                # Calculate match score based on query token occurrences
                match_count = sum(1 for tok in q_tokens if len(tok) >= 3 and tok in norm)
                model_bonus = 10 if model_numbers and any(mn in norm for mn in model_numbers) else 0
                header_bonus = 3 if re.search(r'\b(startup|background|history|deutz-allis|buyout|spin-off|incorporation|growth strategy)\b', norm) else 0

                score = model_bonus + (match_count * 2) + header_bonus
                scored_sentences.append((score, sentence))

        # Sort sentences by match score descending
        scored_sentences.sort(key=lambda x: x[0], reverse=True)

        ordered = [s for score, s in scored_sentences if score > 0]
        if not ordered:
            ordered = [s for score, s in scored_sentences]

        if not ordered:
            return NO_DETAILED_INFO_RESPONSE

        return self._assemble_paragraphs(ordered)

    def _extract_query_tokens(self, question: str) -> List[str]:
        """Extract meaningful query tokens, filtering out stop words."""
        return [
            w.lower()
            for w in re.findall(r'\b[a-zA-Z0-9]{2,}\b', question)
            if w.lower() not in self._stop_words
        ]

    def _strip_noise(self, text: str) -> str:
        """Remove noise headers and formatting artifacts from chunk text."""
        for pattern in self._noise_patterns:
            text = re.sub(pattern, '', text)
        return text

    def _extract_sentences(self, text: str) -> List[str]:
        """Split text into clean sentences."""
        # Join wrapped lines
        clean_text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
        clean_text = re.sub(r'[ \t]+', ' ', clean_text)

        # Split by sentence boundaries or newlines
        raw_sentences = re.split(r'(?<=[.!?])\s+|\n+', clean_text)

        sentences = []
        for s in raw_sentences:
            sentence = s.strip()
            if (
                len(sentence) >= self._min_sentence_length
                and len(sentence.split()) >= self._min_sentence_words
            ):
                # Filter metric table rows
                if not re.search(
                    r'\b(headquarters|annual revenue|stock ticker|global employees)\s+[A-Z0-9]',
                    sentence,
                    re.IGNORECASE,
                ):
                    sentences.append(sentence)

        return sentences

    @staticmethod
    def _clean_sentence(sentence: str) -> str:
        """Normalize capitalization and punctuation."""
        if sentence and not sentence[0].isupper():
            sentence = sentence[0].upper() + sentence[1:]
        if not sentence.endswith(('.', '!', '?')):
            sentence += '.'
        return sentence

    @staticmethod
    def _assemble_paragraphs(sentences: List[str]) -> str:
        """Assemble top sentences into paragraph format."""
        paragraph_1 = " ".join(sentences[:TOP_SENTENCES_PARAGRAPH_1])
        if len(sentences) > TOP_SENTENCES_PARAGRAPH_2_START:
            paragraph_2 = " ".join(
                sentences[TOP_SENTENCES_PARAGRAPH_2_START:TOP_SENTENCES_PARAGRAPH_2_END]
            )
            return f"{paragraph_1}\n\n{paragraph_2}"
        return paragraph_1
