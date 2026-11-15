enum Verdict {
  MergeReady @alias("merge-ready")
  Blocked @alias("blocked")
  HumanGated @alias("human-gated")
  Unverified @alias("unverified")
}

enum ItemKind {
  Behavior @alias("behavior")
  Example @alias("example")
  Edge @alias("edge")
  Error @alias("error")
}

class TestListReady {
  tool string
  items list of test items (unit: only with units)
  gaps string[]
  directives string[]
  units Unit[]?
}

class Escalate {
  tool string
  question string
}

class RedTestReady {
  tool string
  item_id string
  test_file string
  test_name string
  last_assertion_line int
  defeats string
  add_items list of test items
}

class NoCheatLeft {
  tool string
  reason string
}

class GreenReady {
  tool string
  summary string
}

class RefactorDone {
  tool string
  summary string
  changed bool
}

class Review {
  tool string
  verdict Verdict
  findings Finding[]
  verification string
}

class TestItem {
  text string
  kind ItemKind
  source string
  needed string
  criteria int[]
}

class UnitTestItem {
  unit string?
}

class Finding {
  id string
  class_ string @alias("class")
  severity string
  where string
  finding string
  owner string
}

function AnalyzerAnswer(input: string) -> TestListReady | Escalate {
  prompt #"
    {{ ctx.output_format }}
  "#
}

function RedAnswer(input: string) -> RedTestReady | NoCheatLeft | Escalate {
  prompt #"
    {{ ctx.output_format }}
  "#
}

function ReviewAnswer(input: string) -> Review | Escalate {
  prompt #"
    {{ ctx.output_format }}
  "#
}
