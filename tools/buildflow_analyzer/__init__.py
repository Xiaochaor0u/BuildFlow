"""BuildFlow research-efficiency analyzer for Jenkins build history.

Reads real Jenkins build data over the REST API, classifies failures with a
rule-based classifier, and renders CSV / HTML reports.

Only the Python standard library is used, so the tool runs with a bare
Python 3 interpreter (no pip install required).
"""

__version__ = "1.0.0"
