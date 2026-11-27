
            )
            # Small extra pause between categories.
            time.sleep(max(0.2, args.sleep * 0.5))
        finished = ist_now()
        payload = build_payload(
            all_scan=all_scan,
            cat_scans=cat_scans,
            tops=tops,
            started=started,
            finished=finished,
            sleep_s=args.sleep,
        )
    except FetchError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Existing deals.json was not modified.",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"ERROR: unexpected failure: {e}", file=sys.stderr)
        print("Existing deals.json was not modified.", file=sys.stderr)
        return 1
    n = len(payload["items"])
    if n < args.min_items:
        print(
            f"ERROR: only {n} qualifying items (minimum {args.min_items}). "
            "Existing deals.json was not modified.",
            file=sys.stderr,
        )
        return 2
    try:
        write_atomic(out, payload)
    except Exception as e:
        print(f"ERROR: failed to write {out}: {e}", file=sys.stderr)
        return 1
    elapsed = (finished - started).total_seconds()
    print(
        f"Wrote {n} deals to {out} in {elapsed:.1f}s "
        f"({payload['meta']['category_counts']})",
        flush=True,
    )
    return 0
if __name__ == "__main__":
    sys.exit(main())
