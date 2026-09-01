"""The PySpark Plugin: what a Spark job is held to, wherever it lives.

A worked example of detection by import. `.py` is the suffix of a Spark job and
of a Django view and of this file, and holding all three to the DataFrame API
would be the fastest way to make a Plugin something people switch off. So this
Plugin declares no suffix at all: it answers for `pyspark`, and `core.registry`
reads the Python files the frozen Plan names to find out whether any of them
imports it. A repository with Spark jobs in it and a Plan that touches none of
them hears nothing, which is the same bargain the `python` Plugin makes by
declaring no root markers.

One Fragment, keyed to the three Roles that produce or judge code. The Security
Role is absent for the reason it is absent from the `python` Plugin: `.rdd` is
not a vulnerability, and a style convention in a security prompt competes with
the audit rather than supporting it. What the Security Role does need to know
about a Spark platform is a workspace's business, and the `databricks` Plugin
is where that Fragment lives.
"""

from __future__ import annotations

from ...core.contracts import Fragment, Plugin

#: Every line here changes what an Agent produces. Spark conventions an Agent
#: would have followed anyway — name your DataFrames well, do not swallow
#: exceptions — are the `python` Fragment's job or nobody's, and repeating them
#: is tokens spent on agreement.
_CONVENTIONS = """\
Follow these PySpark conventions unless the module you are editing plainly does otherwise:

- Write DataFrame and Column expressions, not RDDs. `.rdd`, `map`, and
  `flatMap` leave the optimiser out of the work, so a job that reaches for them
  pays for a planner it refuses to use. Dropping to RDD for something the
  DataFrame API already does is a defect.
- Reach for `pyspark.sql.functions`, imported as `F`, before writing a UDF. A
  Python UDF serialises every row across the JVM boundary; where one is needed,
  say in a comment which built-in was missing.
- Read with a declared schema. `inferSchema` reads the source twice and makes
  the schema whatever last week's file happened to contain.
- Select the columns you need and join on named keys. A `select("*")` after a
  join carries duplicated names downstream, and the error names the column
  rather than the line that made it.
- Do not `collect()` or `toPandas()` a frame you have not bounded. Aggregate,
  filter, or `limit` first: the driver holds whatever comes back.
- Tests build a local session and assert on small frames; one that needs a
  cluster is not a test this repository can run.\
"""

PYSPARK = Plugin(
    name="pyspark",
    imports=("pyspark",),
    fragments=(
        Fragment(text=_CONVENTIONS, roles=("implementer", "tester", "reviewer")),
    ),
)

__all__ = ["PYSPARK"]
