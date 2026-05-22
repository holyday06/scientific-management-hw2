"""
ConceptKnowledgeStructure 관련 헬퍼.

R3 답변 반영:
- ROOT_DOCUMENT는 depth 0의 빈 기본 노드. 문항 생성에 사용 안 함.
- embedding_vector는 text-embedding-004 형식 (768차원).
- edge는 단방향 쌍 (예: parent_of + child_of)으로 양방향 관계 표현.
"""
from __future__ import annotations

import math
from typing import Optional

from common.enums import EdgeRelation
from common.schemas import ConceptKnowledgeStructure, ConceptNode


# ────────────────────────────────────────────────────────────
# ROOT 노드 처리
# ────────────────────────────────────────────────────────────
ROOT_CONCEPT_ID = "ROOT_DOCUMENT"


def is_root_node(node: ConceptNode) -> bool:
    """ROOT_DOCUMENT 노드인지 확인.

    Exam Planner는 ROOT를 슬롯의 concept으로 배정하면 안 됨.
    """
    return node.concept_id == ROOT_CONCEPT_ID or node.depth_in_tree == 0


def get_assignable_nodes(
    structure: ConceptKnowledgeStructure,
) -> list[ConceptNode]:
    """문항에 실제 사용 가능한 concept 노드들.

    ROOT_DOCUMENT 등 depth=0 노드를 제외.
    """
    return [n for n in structure.nodes if not is_root_node(n)]


# ────────────────────────────────────────────────────────────
# Edge 양방향 traversal helper
# ────────────────────────────────────────────────────────────
def get_neighbors(
    structure: ConceptKnowledgeStructure,
    concept_id: str,
    relations: Optional[set[EdgeRelation]] = None,
) -> set[str]:
    """주어진 concept_id의 이웃 노드 id 집합.

    R3 단방향 쌍 구조에서, 한 edge라도 연결되어 있으면 이웃으로 간주.

    Args:
        structure: 지식 구조
        concept_id: 시작 노드
        relations: 고려할 관계 종류 필터 (None이면 모든 관계)

    Returns:
        이웃 concept_id 집합
    """
    neighbors: set[str] = set()
    for edge in structure.edges:
        if relations and edge.relation not in {r.value for r in relations}:
            continue
        if edge.from_concept_id == concept_id:
            neighbors.add(edge.to_concept_id)
        if edge.to_concept_id == concept_id:
            neighbors.add(edge.from_concept_id)
    return neighbors


def get_max_distance_in_subset(
    structure: ConceptKnowledgeStructure,
    concept_ids: list[str],
) -> int:
    """여러 concept 사이의 최대 그래프 거리.

    Exam Planner가 슬롯의 X₁을 계산할 때 사용.
    primary_concept_ids = [A, B, C]면, A↔B, A↔C, B↔C 중 최댓값.

    >>> # 노드 A·B가 직접 연결되어 있으면 거리 1
    >>> # 단일 노드면 거리 0
    """
    if len(concept_ids) <= 1:
        return 0

    max_dist = 0
    for i, id_a in enumerate(concept_ids):
        for id_b in concept_ids[i + 1:]:
            dist = structure.get_depth_between(id_a, id_b)
            if dist > max_dist:
                max_dist = dist
            # 연결되지 않은 경우(-1)는 무시
    return max_dist


# ────────────────────────────────────────────────────────────
# Embedding helpers (text-embedding-004 = 768차원)
# ────────────────────────────────────────────────────────────
EMBEDDING_DIM = 768


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """두 임베딩의 코사인 유사도. 1.0 = 동일, 0.0 = 직교, -1.0 = 반대.

    Topic_Prioritizer가 concept과 req의 유사도 계산에 사용 가능.
    """
    if len(vec_a) != len(vec_b):
        raise ValueError(
            f"임베딩 차원 불일치: {len(vec_a)} vs {len(vec_b)}"
        )

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def node_to_llm_context(
    node: ConceptNode,
    include_embedding: bool = False,
) -> dict:
    """ConceptNode를 LLM 컨텍스트용 dict로 변환.

    기본은 embedding 제외 (토큰 낭비 방지).
    LLM에 그래프 정보 넘길 때 사용.
    """
    exclude = set() if include_embedding else {"embedding_vector"}
    return node.model_dump(exclude=exclude)


# ────────────────────────────────────────────────────────────
# Smoke test
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from common.enums import DifficultyLevel, EdgeRelation, QuestionType
    from common.schemas import ConceptEdge

    # ROOT + 2개 자식 노드
    nodes = [
        ConceptNode(
            concept_id="ROOT_DOCUMENT",
            concept_name="(root)",
            depth_in_tree=0,
            intrinsic_difficulty_level=DifficultyLevel.L1,
            suitable_question_types=[],
        ),
        ConceptNode(
            concept_id="M1_1_work",
            concept_name="What is Work?",
            depth_in_tree=1,
            parent_concept_id="ROOT_DOCUMENT",
            intrinsic_difficulty_level=DifficultyLevel.L2,
            suitable_question_types=[QuestionType.SHORT_ANSWER, QuestionType.LONG_ANSWER],
        ),
        ConceptNode(
            concept_id="M1_4_taylor",
            concept_name="Taylor's Principles",
            depth_in_tree=1,
            parent_concept_id="ROOT_DOCUMENT",
            intrinsic_difficulty_level=DifficultyLevel.L3,
            suitable_question_types=[QuestionType.LONG_ANSWER, QuestionType.CASE_ANALYSIS],
        ),
    ]
    edges = [
        # R3 답변: 양방향을 쌍으로 표현
        ConceptEdge(from_concept_id="ROOT_DOCUMENT", to_concept_id="M1_1_work",
                    relation=EdgeRelation.PARENT_OF),
        ConceptEdge(from_concept_id="M1_1_work", to_concept_id="ROOT_DOCUMENT",
                    relation=EdgeRelation.CHILD_OF),
        ConceptEdge(from_concept_id="ROOT_DOCUMENT", to_concept_id="M1_4_taylor",
                    relation=EdgeRelation.PARENT_OF),
        ConceptEdge(from_concept_id="M1_4_taylor", to_concept_id="ROOT_DOCUMENT",
                    relation=EdgeRelation.CHILD_OF),
    ]
    structure = ConceptKnowledgeStructure(nodes=nodes, edges=edges)

    # 1. ROOT 필터링
    assignable = get_assignable_nodes(structure)
    assert len(assignable) == 2
    assert all(n.concept_id != "ROOT_DOCUMENT" for n in assignable)
    print(f"✓ get_assignable_nodes: {len(assignable)}개 (ROOT 제외)")

    # 2. 이웃 조회
    neighbors = get_neighbors(structure, "M1_1_work")
    assert "ROOT_DOCUMENT" in neighbors
    print(f"✓ get_neighbors(M1_1_work): {neighbors}")

    # 3. 부분집합 최대 거리
    max_dist = get_max_distance_in_subset(
        structure, ["M1_1_work", "M1_4_taylor"]
    )
    assert max_dist == 2  # via ROOT
    print(f"✓ get_max_distance_in_subset: {max_dist}")

    # 4. 코사인 유사도
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    v3 = [1.0, 1.0, 0.0]
    assert cosine_similarity(v1, v1) == 1.0
    assert cosine_similarity(v1, v2) == 0.0
    print(f"✓ cosine_similarity orthogonal: {cosine_similarity(v1, v2)}")
    print(f"✓ cosine_similarity 45deg: {round(cosine_similarity(v1, v3), 4)}")

    # 5. LLM context (embedding 제외)
    ctx = node_to_llm_context(nodes[1])
    assert "embedding_vector" not in ctx
    print(f"✓ node_to_llm_context: keys = {list(ctx.keys())[:3]}...")

    print("\n✓ All concept_utils smoke tests passed")
