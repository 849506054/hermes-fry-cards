"""Body-area transition text becomes an interim message once a tool call interrupts it."""
from hermes_fry_cards.streaming.segments import SegmentState

PREAMBLE = "先查一下两个配置项。再回来改。"
FINAL = "改好了。两项都已经对上。"


def test_answer_text_before_a_tool_call_is_kept_once():
    state = SegmentState()
    state.on_answer_delta(PREAMBLE)
    state.on_tool_event(1)
    state.on_answer_delta(FINAL)
    state.on_completion_answer(FINAL)
    assert state.completed_process_texts() == [PREAMBLE]


def test_each_interrupted_paragraph_is_kept_in_order():
    state = SegmentState()
    state.on_answer_delta("第一段过渡。")
    state.on_tool_event(1)
    state.on_answer_delta("第二段过渡。")
    state.on_tool_event(2)
    state.on_answer_delta(FINAL)
    state.on_completion_answer(FINAL)
    assert state.completed_process_texts() == ["第一段过渡。", "第二段过渡。"]


def test_turn_without_tool_calls_yields_no_interim_message():
    state = SegmentState()
    state.on_answer_delta(FINAL)
    state.on_completion_answer(FINAL)
    assert state.completed_process_texts() == []


def test_final_answer_text_is_never_recorded_as_progress():
    state = SegmentState()
    state.on_answer_delta(FINAL)
    state.on_tool_event(1)
    state.on_answer_delta(FINAL + " 下一步等你确认。")
    state.on_completion_answer(FINAL + " 下一步等你确认。")
    assert state.completed_process_texts() == []


def test_interim_message_recorded_through_the_thinking_path_is_not_duplicated():
    state = SegmentState()
    state.on_interim_answer(PREAMBLE)
    state.on_tool_event(1)
    state.on_answer_delta(FINAL)
    state.on_completion_answer(FINAL)
    assert state.completed_process_texts() == [PREAMBLE]
