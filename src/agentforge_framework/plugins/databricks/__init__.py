"""The Databricks Plugin: what a workspace's code is held to, and what to audit.

A worked example of two things the other Plugins do not show.

**Detection by root marker alone.** A Databricks repository is one wherever the
work lands: a bundle's `databricks.yml` says the code in this tree is deployed
to a workspace, and that is true of a Plan touching one SQL file and of a Plan
touching one notebook. Unlike `pyspark` there is no import to read — the runtime
binds `spark` and `dbutils` for you, so a notebook that uses the whole platform
can import nothing at all — and unlike `python` the fact is a property of the
repository rather than of the blast radius.

**A Fragment that differs by Role.** What the Security Role needs to know about
a workspace is not what the Implementer needs. The Implementer is writing a
MERGE and needs the idiom this shop writes; the Security Role is auditing the
same file and needs to know where the secrets are meant to come from and which
grant is too wide. One Fragment each, keyed to the Roles that want them, because
the registry hands one Fragment per Plugin to one Role — the Implementer reading
a paragraph about service principals is paying for advice it cannot act on.
"""

from __future__ import annotations

from ...core.contracts import Fragment, Plugin

#: What the Roles that write and review the code are held to. Three-part naming
#: and MERGE are here because they are what a reviewer rejects on sight and what
#: an Implementer reinvents every Run.
_CONVENTIONS = """\
Follow these Databricks conventions unless the code you are editing plainly does otherwise:

- Name every table in full: `catalog.schema.table`. A bare or two-part name
  resolves against whatever `USE` ran last, so it reads correctly in the
  notebook that wrote it and nowhere else. The catalog is configuration — take
  it from the job's parameters rather than hard-coding the production one.
- Upsert with `MERGE INTO`, not delete-then-insert. Two statements are two
  chances to leave the table short, and the window between them is one a
  reader will hit.
- Match a MERGE on the business key, and write `WHEN MATCHED THEN UPDATE SET`
  column by column rather than `SET *`, so a new source column cannot silently
  overwrite a target one.
- Deduplicate the source before a MERGE. Two source rows for one key fail the
  whole statement at run time, which is how a MERGE that passed on a sample
  breaks in production.
- Do not repair a subset of rows with `INSERT OVERWRITE` on a managed table.
  Delete or merge what is wrong.
- Change a table's layout with table properties, `OPTIMIZE`, and `ZORDER`
  rather than by rewriting it. A rewrite breaks every reader mid-flight.\
"""

#: The same repository read as a target rather than as a codebase. Every line is
#: something the Security Role can find in a diff and name a location for, which
#: is what its Findings are made of.
_POSTURE = """\
Audit Databricks code against these, and report what you find rather than fixing it:

- Secrets are fetched at run time from a secret scope — `dbutils.secrets.get`.
  A token, storage key, or JDBC password written into a notebook, a job
  definition, or a checked-in config is a Finding whatever the repository's
  visibility, and so is a secret printed, logged, or written to a table.
- A job runs as a service principal holding its own grants. A personal access
  token in a job definition or a bundle outlives the person it belongs to and
  carries every permission they have.
- Access is granted in Unity Catalog on the narrowest object that answers the
  need. A `GRANT` widened to the schema or the catalog to unblock one query is
  the Finding, and so is `ALL PRIVILEGES` where `SELECT` was the requirement.
- Notebook widgets and job parameters are user input. Concatenated into a SQL
  string they are injection; they belong in bound parameters.
- Sensitive data does not belong on the DBFS root, which every workspace user
  can read. A Unity Catalog volume or an external location is where it goes.\
"""

DATABRICKS = Plugin(
    name="databricks",
    # A bundle's descriptor under either spelling, and the CLI's profile file
    # for a repository that predates bundles. Not `.databricks/`: that directory
    # is local state the tooling writes and the repository ignores, so it says
    # something about one machine rather than about the code.
    root_markers=("databricks.yml", "databricks.yaml", ".databrickscfg"),
    fragments=(
        Fragment(text=_CONVENTIONS, roles=("implementer", "tester", "reviewer")),
        Fragment(text=_POSTURE, roles=("security",)),
    ),
)

__all__ = ["DATABRICKS"]
