"""Dedicated durable quality worker: python -m cli.evaluation_worker --concurrency 2.

Use EVALUATION_EMBEDDED_WORKER=false on the API when using dedicated workers.
Performance scheduling remains owned by the API process.
"""
import argparse
import asyncio


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--concurrency',type=int,default=2,choices=range(1,17))
    args=parser.parse_args(argv)
    from backend.main import root_store,_evaluation_scheduler
    # The API owns schema migration and performance restart recovery. A quality
    # worker must never initialize RunStore: doing so would interrupt active
    # performance tests, dataset builds and AI reports owned by the API.
    with root_store._session() as connection:
        connection.execute("SELECT id FROM evaluation_runs LIMIT 0")
    try:asyncio.run(_evaluation_scheduler(args.concurrency))
    except KeyboardInterrupt:pass


if __name__=='__main__':main()
