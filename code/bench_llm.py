"""Standalone LLM round-trip benchmark.

Isolates the LLM API call latency from Streamlit / pseudonymization /
SharePoint overhead. Reuses the same .env / client-init logic the app uses
(OpenAI direct vs Azure CJBS) and the same SYSTEM_PROMPT_OPENAI, so the
numbers are comparable to TURN_TIMING lines emitted by the running app.

Run from the `code/` directory:

    python bench_llm.py                 # 5 runs (default)
    python bench_llm.py --runs 10
    python bench_llm.py --message "Hallo, ja das stimmt."

Run from Render's web shell to compare Render-network vs your-laptop-network
to the same API endpoint.
"""

import argparse
import logging
import os
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

import config  # noqa: E402  (load_dotenv must run first)


def _load_api_key():
    """Mirror of interview.py:_load_api_key — Azure if both CJBS_* set, else OpenAI."""
    azure_key = os.getenv("CJBS_API_KEY")
    azure_endpoint = os.getenv("CJBS_API_ENDPOINT")
    api_version = os.getenv("CJBS_API_VERSION", "2023-05-15")
    openai_key = os.getenv("OPENAI_API_KEY")
    deployment_name = os.getenv("CJBS_DEPLOYMENT_NAME")

    if not deployment_name:
        raise ValueError("Set CJBS_DEPLOYMENT_NAME in code/.env (e.g. 'gpt-4o').")

    if azure_key and azure_endpoint:
        return "azure", azure_key, azure_endpoint, api_version, deployment_name
    if openai_key:
        return "openai", openai_key, None, None, deployment_name
    raise ValueError(
        "No API credentials. Set OPENAI_API_KEY or CJBS_API_KEY+CJBS_API_ENDPOINT."
    )


def _make_client():
    provider, key, endpoint, version, deployment = _load_api_key()
    if provider == "azure":
        from openai import AzureOpenAI
        return AzureOpenAI(api_key=key, api_version=version, azure_endpoint=endpoint), provider, deployment
    from openai import OpenAI
    return OpenAI(api_key=key), provider, deployment


def _one_run(client, deployment, system_prompt, user_message):
    """Stream a single completion; return (ttft_ms, ttlt_ms, n_chunks, out_chars)."""
    t0 = time.perf_counter()
    stream = client.chat.completions.create(
        model=deployment,
        max_completion_tokens=config.MAX_OUTPUT_TOKENS,
        stream=True,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    ttft_ms = -1.0
    out = ""
    n_chunks = 0
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            if ttft_ms < 0:
                ttft_ms = (time.perf_counter() - t0) * 1000
            out += chunk.choices[0].delta.content
            n_chunks += 1
    ttlt_ms = (time.perf_counter() - t0) * 1000
    return ttft_ms, ttlt_ms, n_chunks, len(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5, help="Number of runs (default: 5)")
    parser.add_argument(
        "--message",
        default="Hallo, ich beginne gern mit dem Interview.",
        help="User message to send.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    client, provider, deployment = _make_client()
    print(
        f"Provider: {provider} | Deployment: {deployment} | "
        f"Endpoint: {os.getenv('CJBS_API_ENDPOINT', 'api.openai.com')}",
        file=sys.stderr,
    )
    print(f"System prompt length: {len(config.SYSTEM_PROMPT_OPENAI)} chars", file=sys.stderr)
    print(f"User message: {args.message!r}\n", file=sys.stderr)

    results = []
    for i in range(1, args.runs + 1):
        try:
            ttft, ttlt, n_chunks, out_chars = _one_run(
                client, deployment, config.SYSTEM_PROMPT_OPENAI, args.message,
            )
        except Exception as exc:
            print(f"[run {i}/{args.runs}] FAILED: {exc}", file=sys.stderr)
            continue
        results.append((ttft, ttlt, n_chunks, out_chars))
        print(
            f"[run {i}/{args.runs}] ttft={ttft:7.1f}ms  ttlt={ttlt:7.1f}ms  "
            f"chunks={n_chunks:4d}  out_chars={out_chars}"
        )

    if not results:
        print("\nNo successful runs.", file=sys.stderr)
        sys.exit(1)

    ttfts = [r[0] for r in results]
    ttlts = [r[1] for r in results]
    print()
    print(f"Median ttft: {statistics.median(ttfts):.1f} ms  "
          f"(min {min(ttfts):.1f}, max {max(ttfts):.1f})")
    print(f"Median ttlt: {statistics.median(ttlts):.1f} ms  "
          f"(min {min(ttlts):.1f}, max {max(ttlts):.1f})")


if __name__ == "__main__":
    main()
