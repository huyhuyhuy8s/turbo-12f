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
  tool "test_list_ready"
  items UnitTestItem[]
  gaps string[]
  directives string[]
  units Unit[]?
}

class Escalate {
  tool "escalate"
  question string
}

class RedTestReady {
  tool "red_test_ready"
  item_id string
  test_file string
  test_name string
  last_assertion_line int
  defeats string
  add_items TestItem[]
}

class NoCheatLeft {
  tool "no_cheat_left"
  reason string
}

class GreenReady {
  tool "green_ready"
  summary string
}

class RefactorDone {
  tool "refactor_done"
  summary string
  changed bool
}

class Review {
  tool "review"
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
  text string
  kind ItemKind
  source string
  needed string
  criteria int[]
  unit string?
}

class Unit {
  id string
  owns string[]
  tests string[]
  after string[]
  needed string
}

class Finding {
  id string
  class_ string @alias("class")
  severity string
  where string
  finding string
  owner string
}

function AnalyzerAnswer(task: string) -> TestListReady | Escalate {
  client Worker
  prompt #"
    {{ ctx.output_format }}
  "#
}

function RedAnswer(task: string) -> RedTestReady | NoCheatLeft | Escalate {
  client Worker
  prompt #"
    {{ ctx.output_format }}
  "#
}

function ReviewAnswer(task: string) -> Review | Escalate {
  client Worker
  prompt #"
    {{ ctx.output_format }}
  "#
}

client<llm> Worker {
  provider openai
  options {
    model "gpt-4o"
  }
}
