# Sandbox for untrusted student Python.
#
# Deliberately BARE. pda-grader's image carries pandas/scikit-learn/nbclient because it
# runs notebooks; this one only calls solve(*input), so a slim base cuts container
# startup from tens of seconds to about one — and startup is paid on every batch.
FROM python:3.11-slim

# Non-root, no shell, no home. Combined with --read-only and --network none in runner.py,
# there is very little for hostile code to reach even before the WASM-free isolation
# arguments start.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin judge
WORKDIR /app
COPY harness.py compare.py /app/
USER judge
ENTRYPOINT []
