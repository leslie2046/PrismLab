"""Dedicated worker for durable benchmarks, dataset builds and AI reports."""
import argparse
import asyncio


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=2, choices=range(1, 17))
    args = parser.parse_args(argv)
    from backend.main import root_store, _background_scheduler

    # API startup owns schema creation and recovery. The worker only verifies
    # that its queue table exists before claiming jobs.
    with root_store._session() as connection:
        connection.execute("SELECT id FROM background_jobs LIMIT 0")
    try:
        asyncio.run(_background_scheduler(args.concurrency))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
