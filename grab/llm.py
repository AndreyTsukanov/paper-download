"""Single place for the cheap disambiguation/verification LLM.

One provider-configurable client (OpenAI `gpt-4.1-mini` by default, Anthropic
optional). Only this client may use `GRAB_LLM_PROXY`; academic traffic stays
proxy-less. The key is optional -- without it `has_key()` is False and the tool
runs fully free (ambiguous titles are surfaced, gray-zone PDFs stay unverified).
"""
import os
import re
from typing import List, Dict, Optional


class LLMClient:
    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider or os.environ.get("GRAB_LLM_PROVIDER", "openai")
        self.model = model or os.environ.get("GRAB_MODEL", "gpt-4.1-mini")

    def has_key(self) -> bool:
        var = "ANTHROPIC_API_KEY" if self.provider == "anthropic" else "OPENAI_API_KEY"
        return bool(os.environ.get(var))

    def complete(self, prompt: str, max_tokens: int = 8) -> str:
        """One-shot completion via the configured provider. Returns raw text."""
        if self.provider == "anthropic":
            import anthropic
            msg = anthropic.Anthropic().messages.create(
                model=self.model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

        import openai
        proxy = os.environ.get("GRAB_LLM_PROXY")
        if proxy:
            import httpx
            try:
                http_client = httpx.Client(proxy=proxy, trust_env=False)
            except TypeError:  # httpx < 0.26 uses the plural kwarg
                http_client = httpx.Client(proxies=proxy, trust_env=False)
            client = openai.OpenAI(http_client=http_client)
        else:
            client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""

    def pick(self, query: str, papers: List[Dict]) -> Optional[int]:
        """Index of the candidate that is the SAME paper, or None (also for -1/none/error)."""
        lines = [
            f"[{i}] {p.get('title', '')} — {p.get('authors', '')} "
            f"({(p.get('published_date') or '')[:4]}) [{p.get('source', '')}]"
            for i, p in enumerate(papers)
        ]
        prompt = (
            'A user is looking for this exact paper:\n'
            f'  "{query}"\n\n'
            'Candidates:\n' + "\n".join(lines) +
            "\n\nReply with ONLY the bracket number of the candidate that is the SAME paper "
            "(same study/title), or -1 if none of them is clearly that paper. "
            "Do NOT pick a merely topically-similar paper — prefer -1 when unsure."
        )
        try:
            text = self.complete(prompt, max_tokens=8)
        except Exception:
            return None
        m = re.search(r"-?\d+", text or "")
        if not m:
            return None
        idx = int(m.group())
        return None if idx < 0 else idx

    def verify_match(self, query: str, snippet: str) -> Optional[bool]:
        """True/False whether the PDF text is the requested paper; None if no key/unsure/error."""
        if not self.has_key():
            return None
        prompt = (
            "You verify whether a downloaded PDF is the SAME paper the user asked for.\n"
            f'User asked for:\n  "{query}"\n\n'
            "First-page text extracted from the downloaded PDF (truncated):\n"
            f'"""{(snippet or "")[:2000]}"""\n\n'
            "Answer ONLY 'yes' if this PDF is that same paper, or 'no' otherwise."
        )
        try:
            text = self.complete(prompt, max_tokens=4).strip().lower()
        except Exception:
            return None
        if "yes" in text:
            return True
        if "no" in text:
            return False
        return None
