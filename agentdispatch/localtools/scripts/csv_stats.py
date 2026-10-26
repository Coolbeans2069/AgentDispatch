"""tabular probe: shape, dtypes, nulls, numeric describe for CSV/TSV/Parquet (pandas / pyarrow)."""
import json, os, sys


def main(argv):
    path = argv[0] if argv else ""
    if not path or not os.path.isfile(path):
        print(json.dumps(dict(ok=False, error="give a CSV/TSV/Parquet file")))
        return 2
    if os.path.getsize(path) > 200 * 1048576:
        print(json.dumps(dict(ok=False, error="file larger than 200 MB")))
        return 3
    try:
        import pandas as pd
    except ImportError as e:
        print(json.dumps(dict(ok=False, error="pandas missing: %s" % e)))
        return 4
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".parquet", ".pq"):
            df = pd.read_parquet(path)
        elif ext == ".tsv":
            df = pd.read_csv(path, sep="\t", nrows=50000)
        else:
            df = pd.read_csv(path, nrows=50000)
    except Exception as e:
        print(json.dumps(dict(ok=False, error="read failed: %s" % str(e)[:200])))
        return 5
    desc = {}
    try:
        d = df.describe(include="all").fillna("").astype(str)
        desc = {c: {i: str(d.loc[i, c])[:80] for i in list(d.index)[:12]} for c in list(d.columns)[:30]}
    except Exception:
        pass
    nulls = {c: int(df[c].isna().sum()) for c in list(df.columns)[:60]}
    sample = df.head(5).astype(str).to_dict(orient="records")
    print(json.dumps(dict(ok=True, rows=int(len(df)), cols=int(df.shape[1]), columns=[str(c)[:80] for c in df.columns[:60]],
                          dtypes={str(c)[:80]: str(t) for c, t in list(df.dtypes.items())[:60]}, nulls=nulls, describe=desc, sample=sample), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
