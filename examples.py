"""
Examples demonstrating all features of DAG Flow.

Run this file directly to see the engine in action::

    python -m dag_flow.examples
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Set up logging so we can see what's happening
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

from . import DAG, DAGExecutionError, Node, RetryPolicy, load_dag

# ═══════════════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════════════


async def sleep_ms(ms: float) -> None:
    """Simulate async work."""
    await asyncio.sleep(ms / 1000)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Serial execution (chain)
# ═══════════════════════════════════════════════════════════════════════════


async def demo_serial():
    """A → B → C — each node depends on the previous one."""
    print("\n" + "=" * 60)
    print("  1. SERIAL EXECUTION  (A -> B -> C)")
    print("=" * 60)

    dag = DAG("serial_demo")

    @dag.node("A")
    async def node_a(ctx: Dict[str, Any]) -> str:
        await sleep_ms(100)
        return "data_from_A"

    @dag.node("B", depends_on=["A"])
    async def node_b(ctx: Dict[str, Any]) -> str:
        data = ctx["A"]
        await sleep_ms(100)
        return f"processed({data})"

    @dag.node("C", depends_on=["B"])
    async def node_c(ctx: Dict[str, Any]) -> str:
        data = ctx["B"]
        await sleep_ms(100)
        return f"finalized({data})"

    results = await dag.run()
    for name in ["A", "B", "C"]:
        r = results[name]
        print(f"  {name}: {r.status.value}  ->  {r.output}")
    print("  [OK] Serial chain completed successfully\n")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Parallel execution
# ═══════════════════════════════════════════════════════════════════════════


async def demo_parallel():
    """A → B, A → C, A → D — B/C/D run in parallel after A completes."""
    print("=" * 60)
    print("  2. PARALLEL EXECUTION  (A -> B|C|D -> E)")
    print("=" * 60)

    dag = DAG("parallel_demo")

    @dag.node("fetch")
    async def fetch(ctx: Dict[str, Any]) -> str:
        await sleep_ms(80)
        return "raw_data"

    @dag.node("enrich", depends_on=["fetch"])
    async def enrich(ctx: Dict[str, Any]) -> str:
        await sleep_ms(150)
        return f"enriched({ctx['fetch']})"

    @dag.node("validate", depends_on=["fetch"])
    async def validate(ctx: Dict[str, Any]) -> str:
        await sleep_ms(120)
        return f"validated({ctx['fetch']})"

    @dag.node("normalize", depends_on=["fetch"])
    async def normalize(ctx: Dict[str, Any]) -> str:
        await sleep_ms(100)
        return f"normalized({ctx['fetch']})"

    @dag.node("merge", depends_on=["enrich", "validate", "normalize"])
    async def merge(ctx: Dict[str, Any]) -> str:
        parts = [ctx["enrich"], ctx["validate"], ctx["normalize"]]
        return " | ".join(parts)

    t0 = time.perf_counter()
    results = await dag.run()
    elapsed = time.perf_counter() - t0

    for r in results.values():
        if r.is_success:
            print(f"  {r.node_name}: OK ({r.duration_ms:.0f} ms)")
    # Parallel work should be noticeably faster than sequential sum
    print(f"  Total wall-clock: {elapsed:.2f}s  (serial would be ~0.45s)")
    print("  [OK] Parallel execution completed\n")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Branching (conditional execution)
# ═══════════════════════════════════════════════════════════════════════════


async def demo_branching():
    """Router → premium_path or standard_path based on input data."""
    print("=" * 60)
    print("  3. BRANCHING  (router -> premium | standard)")
    print("=" * 60)

    dag = DAG("branch_demo")

    @dag.node("router")
    async def router(ctx: Dict[str, Any]) -> dict:
        user = ctx.get("user", {"tier": "standard"})
        await sleep_ms(50)
        return user

    @dag.node(
        "premium_flow",
        depends_on=["router"],
        condition=lambda ctx: ctx["router"].get("tier") == "premium",
    )
    async def premium_flow(ctx: Dict[str, Any]) -> str:
        await sleep_ms(100)
        return f"[PREMIUM] Treatment for {ctx['router']['name']}"

    @dag.node(
        "standard_flow",
        depends_on=["router"],
        condition=lambda ctx: ctx["router"].get("tier") != "premium",
    )
    async def standard_flow(ctx: Dict[str, Any]) -> str:
        await sleep_ms(100)
        return f"[STANDARD] Treatment for {ctx['router']['name']}"

    @dag.node("notify", depends_on=["premium_flow", "standard_flow"])
    async def notify(ctx: Dict[str, Any]) -> str:
        result = ctx.get("premium_flow") or ctx.get("standard_flow")
        return f"Notified: {result}"

    # Run with a premium user
    print("  -- User: premium --")
    results = await dag.run({"user": {"name": "Alice", "tier": "premium"}})
    for name, r in results.items():
        print(f"  {name}: {r.status.value}  ->  {r.output}")

    # Run with a standard user
    print("  -- User: standard --")
    results = await dag.run({"user": {"name": "Bob", "tier": "standard"}})
    for name, r in results.items():
        print(f"  {name}: {r.status.value}  ->  {r.output}")

    print("  [OK] Branching completed\n")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Loop
# ═══════════════════════════════════════════════════════════════════════════


async def demo_loop():
    """Loop a sub-DAG that processes items one at a time."""
    print("=" * 60)
    print("  4. LOOP  (process items one-by-one)")
    print("=" * 60)

    dag = DAG("loop_demo")

    @dag.node("prepare")
    async def prepare(ctx: Dict[str, Any]) -> list:
        items = ["apple", "lemon", "grape", "orange"]
        print(f"  Items to process: {items}")
        return items

    async def process_one(ctx: Dict[str, Any]) -> str:
        await sleep_ms(80)
        idx = ctx.get("advance", {}).get("idx", 0)  # idx from previous iteration's advance output
        item = ctx["prepare"][idx]
        print(f"    -> processed: {item}")
        return item

    async def advance(ctx: Dict[str, Any]) -> dict:
        await sleep_ms(20)
        idx = ctx.get("advance", {}).get("idx", 0)
        return {"idx": idx + 1}

    body_nodes = [
        Node(name="process_one", func=process_one),
        Node(name="advance", func=advance, depends_on=["process_one"]),
    ]

    dag.loop_node(
        "batch_loop",
        body_nodes=body_nodes,
        depends_on=["prepare"],
        condition=lambda ctx, i: ctx["advance"]["idx"] < len(ctx["prepare"]),
        max_iterations=10,
    )

    results = await dag.run()
    for name, r in results.items():
        print(f"  {name}: {r.status.value}")
    print("  [OK] Loop completed\n")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Exception retry
# ═══════════════════════════════════════════════════════════════════════════


async def demo_retry():
    """A flaky node that succeeds on the 3rd attempt."""
    print("=" * 60)
    print("  5. RETRY  (flaky node, succeeds on 3rd try)")
    print("=" * 60)

    dag = DAG("retry_demo")

    attempt_counter = {"calls": 0}

    @dag.node(
        "flaky_api",
        retry=RetryPolicy(
            max_retries=4,
            backoff_base=0.1,
            backoff_factor=2.0,
            backoff_max=2.0,
            retry_on=(RuntimeError, ConnectionError),
            jitter=False,  # deterministic for the demo
        ),
    )
    async def flaky_api(ctx: Dict[str, Any]) -> str:
        attempt_counter["calls"] += 1
        call = attempt_counter["calls"]
        await sleep_ms(50)
        if call < 3:
            raise RuntimeError(f"Transient failure (call #{call})")
        return f"Success on call #{call}!"

    @dag.node("consume", depends_on=["flaky_api"])
    async def consume(ctx: Dict[str, Any]) -> str:
        return f"Consumed: {ctx['flaky_api']}"

    results = await dag.run()

    r = results["flaky_api"]
    print(f"  flaky_api: {r.status.value}  (attempts: {r.attempts})  ->  {r.output}")
    print(f"  consume:   {results['consume'].status.value}  ->  {results['consume'].output}")
    print("  [OK] Retry completed\n")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Realistic workflow: data pipeline
# ═══════════════════════════════════════════════════════════════════════════


async def demo_pipeline():
    """A realistic multi-stage data pipeline with mixed parallelism."""
    print("=" * 60)
    print("  6. REALISTIC PIPELINE")
    print("=" * 60)

    dag = DAG("data_pipeline")

    @dag.node("fetch_users", retry=RetryPolicy(max_retries=2, backoff_base=0.05))
    async def fetch_users(ctx: Dict[str, Any]) -> list:
        await sleep_ms(150)
        return [{"id": i, "name": f"user_{i}"} for i in range(10)]

    @dag.node("fetch_orders", retry=RetryPolicy(max_retries=2, backoff_base=0.05))
    async def fetch_orders(ctx: Dict[str, Any]) -> list:
        await sleep_ms(200)
        return [{"user_id": i % 10, "amount": random.randint(10, 500)} for i in range(30)]

    @dag.node("enrich_users", depends_on=["fetch_users"])
    async def enrich_users(ctx: Dict[str, Any]) -> list:
        await sleep_ms(100)
        users = ctx["fetch_users"]
        for u in users:
            u["tier"] = "premium" if random.random() > 0.7 else "standard"
        return users

    @dag.node("aggregate_orders", depends_on=["fetch_orders"])
    async def aggregate_orders(ctx: Dict[str, Any]) -> dict:
        await sleep_ms(120)
        orders = ctx["fetch_orders"]
        by_user: dict = {}
        for o in orders:
            uid = o["user_id"]
            by_user.setdefault(uid, []).append(o["amount"])
        return {uid: {"count": len(amts), "total": sum(amts)} for uid, amts in by_user.items()}

    @dag.node("join", depends_on=["enrich_users", "aggregate_orders"])
    async def join(ctx: Dict[str, Any]) -> list:
        await sleep_ms(80)
        users = ctx["enrich_users"]
        agg = ctx["aggregate_orders"]
        result = []
        for u in users:
            stats = agg.get(u["id"], {"count": 0, "total": 0})
            result.append({**u, **stats})
        return result

    @dag.node(
        "high_value_report",
        depends_on=["join"],
        condition=lambda ctx: any(r["total"] > 0 for r in ctx["join"]),
    )
    async def high_value_report(ctx: Dict[str, Any]) -> str:
        top = sorted(ctx["join"], key=lambda r: r["total"], reverse=True)[:3]
        lines = [f"  #{i+1}: {r['name']} (${r['total']}, {r['count']} orders, {r['tier']})"
                 for i, r in enumerate(top)]
        return "Top customers:\n" + "\n".join(lines)

    @dag.node("summary", depends_on=["join"])
    async def summary(ctx: Dict[str, Any]) -> dict:
        data = ctx["join"]
        total_revenue = sum(r["total"] for r in data)
        return {
            "total_users": len(data),
            "total_revenue": total_revenue,
            "premium_count": sum(1 for r in data if r["tier"] == "premium"),
        }

    print(f"  Topological order: {' -> '.join(dag.topological_order())}")
    print(f"  Mermaid:\n{dag.to_mermaid()}\n")

    t0 = time.perf_counter()
    results = await dag.run()
    elapsed = time.perf_counter() - t0

    for r in results.values():
        extra = ""
        if r.is_skipped:
            extra = " (skipped - condition)"
        elif r.is_success:
            extra = f" ({r.duration_ms:.0f} ms)"
        print(f"  {r.node_name}: {r.status.value}{extra}")
        if r.is_success and isinstance(r.output, str) and "\n" in r.output:
            print(r.output)

    print(f"\n  Summary: {results['summary'].output}")
    print(f"  Wall-clock: {elapsed:.2f}s")
    print("  [OK] Pipeline completed\n")


# ═══════════════════════════════════════════════════════════════════════════
# 7. Error handling & cascade skip
# ═══════════════════════════════════════════════════════════════════════════


async def demo_error_handling():
    """When a node fails, downstream nodes are skipped."""
    print("=" * 60)
    print("  7. ERROR HANDLING & CASCADE SKIP")
    print("=" * 60)

    dag = DAG("error_demo")

    @dag.node("critical_fetch", retry=RetryPolicy(max_retries=1, backoff_base=0.05))
    async def critical_fetch(ctx: Dict[str, Any]) -> str:
        raise ConnectionError("Service unreachable")

    @dag.node("dependant", depends_on=["critical_fetch"])
    async def dependant(ctx: Dict[str, Any]) -> str:
        return "This should not run"

    @dag.node("independent")
    async def independent(ctx: Dict[str, Any]) -> str:
        await sleep_ms(50)
        return "I run regardless"

    try:
        results = await dag.run()
    except DAGExecutionError as exc:
        results = exc.results
        print(f"  Caught: {exc}")

    for name, r in results.items():
        detail = r.output if r.is_success else (str(r.error) if r.error else "")
        print(f"  {name}: {r.status.value}  {detail}")
    print("  [OK] Error handling demonstrated\n")


# ═══════════════════════════════════════════════════════════════════════════
# 8. Human review (human-in-the-loop)
# ═══════════════════════════════════════════════════════════════════════════


async def demo_human_review():
    """A pipeline that pauses for a human to approve before publishing."""
    print("=" * 60)
    print("  8. HUMAN REVIEW  (human-in-the-loop approval)")
    print("=" * 60)

    dag = DAG("human_review_demo")

    @dag.node("fetch_article")
    async def fetch_article(ctx: Dict[str, Any]) -> dict:
        await sleep_ms(50)
        return {"title": "DAG Flow v0.1", "content": "Hello, human review!"}

    @dag.node("format_article", depends_on=["fetch_article"])
    async def format_article(ctx: Dict[str, Any]) -> str:
        article = ctx["fetch_article"]
        return f"{article['title']} — {article['content']}"

    dag.human_node(
        "review_article",
        depends_on=["format_article"],
        prompt="Please check the formatted article above.",
    )

    @dag.node("publish", depends_on=["review_article"])
    async def publish(ctx: Dict[str, Any]) -> str:
        reviewed = ctx["review_article"]
        return f"Published: {reviewed['payload']['format_article']}"

    try:
        results = await dag.run()
    except DAGExecutionError as exc:
        results = exc.results
        print(f"  Caught: {exc}")

    for name, r in results.items():
        detail = r.output if r.is_success else (str(r.error) if r.error else "")
        print(f"  {name}: {r.status.value}  {detail}")

    if results["review_article"].is_success:
        print("  [OK] Approved & published\n")
    else:
        print("  [OK] Rejected - publish skipped (cascade)\n")


# ═══════════════════════════════════════════════════════════════════════════
# 9. Declarative YAML config
# ═══════════════════════════════════════════════════════════════════════════


async def demo_yaml_config():
    """The same engine, wired from a YAML file instead of Python code."""
    print("=" * 60)
    print("  9. YAML CONFIG  (declarative wiring)")
    print("=" * 60)

    # Only the actual work is code — the wiring lives in pipeline.yaml
    async def cfg_fetch(ctx: Dict[str, Any]) -> dict:
        await sleep_ms(50)
        return {"title": "DAG Flow v0.1", "body": "  declarative config rocks  "}

    async def cfg_clean(ctx: Dict[str, Any]) -> str:
        return ctx["fetch"]["body"].strip()

    async def cfg_enrich(ctx: Dict[str, Any]) -> str:
        await sleep_ms(50)
        return f"[ENRICHED] {ctx['fetch']['title']}"

    async def cfg_merge(ctx: Dict[str, Any]) -> dict:
        return {"title": ctx["enrich"], "body": ctx["clean"]}

    async def cfg_publish(ctx: Dict[str, Any]) -> str:
        reviewed = ctx["review"]
        return f"Published: {reviewed['payload']['merge']['title']}"

    def cfg_needs_report(ctx: Dict[str, Any]) -> bool:
        return bool(ctx.get("merge"))

    async def cfg_report(ctx: Dict[str, Any]) -> str:
        return f"Report generated for {ctx['merge']['title']}"

    functions = {
        "cfg_fetch": cfg_fetch,
        "cfg_clean": cfg_clean,
        "cfg_enrich": cfg_enrich,
        "cfg_merge": cfg_merge,
        "cfg_publish": cfg_publish,
        "cfg_needs_report": cfg_needs_report,
        "cfg_report": cfg_report,
    }

    dag = load_dag(Path(__file__).parent / "pipeline.yaml", functions=functions)
    print(f"  Loaded pipeline.yaml: {' -> '.join(dag.topological_order())}")

    try:
        results = await dag.run()
    except DAGExecutionError as exc:
        results = exc.results
        print(f"  Caught: {exc}")

    for name, r in results.items():
        detail = r.output if r.is_success else (str(r.error) if r.error else "")
        print(f"  {name}: {r.status.value}  {detail}")

    if results["review"].is_success:
        print("  [OK] YAML pipeline completed\n")
    else:
        print("  [OK] Rejected at review - publish skipped (cascade)\n")


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════


async def main():
    print("\n" + "=" * 60)
    print("  DAG Flow - Examples")
    print("=" * 60)

    await demo_serial()
    await demo_parallel()
    await demo_branching()
    await demo_loop()
    await demo_retry()
    await demo_pipeline()
    await demo_error_handling()
    await demo_human_review()
    await demo_yaml_config()

    print("=" * 60)
    print("  All examples completed successfully!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
