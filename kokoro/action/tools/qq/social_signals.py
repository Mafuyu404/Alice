"""QQ social signal and conversation relation helpers."""

from kokoro.action.tools.qq.social_attention import _detect_attention_lines, _detect_relation_lines
from kokoro.action.tools.qq.social_guards import (
    _looks_like_misowned_task,
    _looks_like_search_request,
    _looks_like_social_feedback,
    _looks_like_technical_advice,
    _looks_like_unbacked_action_promise,
    _recent_self_said_quiet,
    _recent_social_feedback,
    _search_topic_from_recent_context,
)
from kokoro.action.tools.qq.social_identity import (
    _content_is_only_name_call,
    _extract_nonself_target,
    _is_self_message,
    _matched_self_alias,
    _participant_names,
    _reply_targets_self,
    _self_aliases,
    _strip_cq_codes,
)
from kokoro.action.tools.qq.social_turns import (
    _clean_recall_anchor,
    _format_packet_for_decision,
    _latest_attention_message,
    _packet_turn_key,
    _recall_anchors_for_messages,
)
