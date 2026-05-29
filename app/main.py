from typing import Annotated, List, TypedDict, Optional, Literal
import operator
import random

from config import logger, settings
from prompts import WRITER_PROMPT, MANAGER_PROMPT, fallback_warning

from pydantic import BaseModel

from langchain_groq import ChatGroq
from langchain_community.tools import DuckDuckGoSearchResults
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from langgraph.graph import StateGraph, START, END


from requests.exceptions import RequestException

# =========================================================
# INITIALIZATION
# =========================================================

api_key = settings.groq_api_key

search = DuckDuckGoSearchResults()

emb_model = HuggingFaceEmbeddings()


senior_llm = ChatGroq(
    api_key=api_key,
    temperature=0,
    model="llama-3.3-70b-versatile"
)

junior_llm = ChatGroq(
    api_key=api_key,
    temperature=0,
    model="llama-3.1-8b-instant"
)


# =========================================================
# STATE
# =========================================================

# State:
# Shared memory passed between all workflow nodes.

class circuitState(TypedDict):
    query: str
    route: str
    error_status: bool
    fallback_raised: bool
    message: Annotated[List[str], operator.add]
    local_db_path: Optional[str]
    data: str
    final_res: str


# =========================================================
# ROUTING SCHEMA
# =========================================================

# Routing:
# use for conditonal routing from manager to search/fallback_node.

class CheckQuery(BaseModel):
    route: Literal[
        "search_node",
        "fallback_node"
    ]
    reason: str
    confidence: float


# =========================================================
# SEARCH NODE
# =========================================================

# Search Node:
# Attempts external retrieval with retry logic.
# Routes to fallback after max failures.

def search_node(state: circuitState):

    query = state["query"]
    logger.info(
        f"Search node started for query: {query}"
    )

    for attempt in range(
        settings.MAX_RETRIES
    ):

        try:

            logger.info(
                f"Search attempt "
                f"{attempt+1}"
            )

            # Simulated failure testing
            if random.randint(0, 9) <= 5:

                raise ConnectionError(
                    "Simulated network failure"
                )

            response = search.invoke(query)

            logger.info(
                "External search successful"
            )

            return {

                "route": "writer",

                "error_status": False,

                "fallback_raised": False,

                "data": response,

                "message": [
                    f"Search successful "
                    f"on attempt {attempt+1}"
                ]
            }

        except RequestException as e:

            logger.warning(
                f"Search attempt "
                f"{attempt+1} failed: {e}"
            )
        
        except Exception as e:
            logger.warning(
                f"Search attempt "
                f"{attempt+1} failed: {e}"
            )

    logger.error(
        "All search retries failed. "
        "Routing to fallback node."
    )

    return {

        "route": "fallback_node",

        "error_status": True,

        "fallback_raised": True,

        "message": [
            "Search failed after max retries"
        ]
    }


# =========================================================
# FALLBACK NODE
# =========================================================

# Fallback Node:
# Uses local vector database when external systems fail.

def fall_back_node(state: circuitState):

    query = state["query"]

    path = state["local_db_path"]

    logger.warning(
        "Fallback node activated"
    )

    if not path:

        logger.error(
            "No local vector database path found"
        )

        return {

            "message": [
                "No local database path found"
            ],
            "data": ""
        }

    try:

        vec_store = Chroma(

            embedding_function=emb_model,

            persist_directory=path
        )

        raw_results = vec_store.similarity_search(
            query,
            k=3
        )

        fallback_text = "\n\n".join(
            [doc.page_content for doc in raw_results]
        )

        logger.info(
            "Fallback vector retrieval successful"
        )

        return {

            "error_status": True,
            "message": [
                "SYSTEM DEGRADED - USING LOCAL KNOWLEDGE"
            ],
            "data": fallback_text
        }

    except Exception as e:

        logger.error(
            f"Fallback node failed: {e}"
        )

        return {

            "message": [
                str(e)
            ],
            "data": ""
        }


# =========================================================
# WRITER NODE
# =========================================================

# Writer Node:
# Generates final user-facing response from retrieved context.

def writer_node(state: circuitState):

    logger.info(
        "Writer node started"
    )

    query = state.get("query", "")

    msg = (
        state["message"][-1]
        if state["message"]
        else "No system message available."
    )

    data = state.get("data", "")

    if state.get("fallback_raised"):
        fall_back_warning = fallback_warning
    else:
        fall_back_warning = ""

    if not data:

        logger.warning(
            "Writer node received empty data"
        )

        return {

            "final_res":
                "No reliable data was available "
                "to generate a response.",
            "message": [
                "Writer node received empty data"
            ]
        }

    final_prompt = f"""

    Here is your required data for writing report.

    {fall_back_warning}

    User Query:
    {query}

    System Message:
    {msg}

    Retrieved Data:
    {data}

    """

    try:

        response = senior_llm.invoke(
            f"{WRITER_PROMPT}\n\n{final_prompt}"
        )

        logger.info(
            "Writer node completed successfully"
        )

        return {

            "final_res": response.content,
            "message": [
                "Writer node generated final response"
            ]
        }

    except Exception as e:

        logger.error(
            f"Writer node failed: {e}"
        )

        return {

            "final_res":
                "Failed to generate final response.",
            "message": [
                str(e)
            ]
        }


# =========================================================
# MANAGER NODE
# =========================================================

# Manager Node:
# Decides whether the workflow should use external search
# or degrade to the fallback retrieval system.

def manager_node(state: circuitState):

    logger.info(
        "Manager node started"
    )
    query = state["query"]
    fall_back = state.get(
        "fallback_raised",
        False
    )
    error_status = state.get(
        "error_status",
        False
    )
    final_res = state.get(
        "final_res",
        ""
    )
    msg = state.get(
        "message",
        []
    )

    final_prompt = f"""

    User Query:
    {query}

    System Message:
    {msg}

    Fallback Raised:
    {fall_back}

    Error Status:
    {error_status}

    Final Response:
    {final_res}

    """

    for attempt in range(
        settings.MAX_RETRIES
    ):

        try:

            struct_res = (
                senior_llm
                .with_structured_output(
                    CheckQuery
                )
            )

            response = struct_res.invoke(
                f"{MANAGER_PROMPT}\n\n{final_prompt}"
            )

            logger.info(
                f"Manager selected route: "
                f"{response.route}"
            )

            return {

                "route": response.route,
                "message": [
                    f"Manager routed to "
                    f"{response.route}"
                ]
            }

        except Exception as e:

            logger.warning(
                f"Retry {attempt+1} failed: {e}"
            )

    logger.error(
        "Manager failed after max retries"
    )

    return {

        "route": "fallback_node",
        "error_status": True,
        "fallback_raised": True,
        "message": [
            "Manager failed after max retries"
        ]
    }


# =========================================================
# ROUTER
# =========================================================

def router_condition(state: circuitState):

    return state["route"]


# =========================================================
# WORKFLOW
# =========================================================

workflow = StateGraph(circuitState)


# Nodes
workflow.add_node(
    "manager_node",
    manager_node
)

workflow.add_node(
    "search_node",
    search_node
)

workflow.add_node(
    "fallback_node",
    fall_back_node
)

workflow.add_node(
    "writer_node",
    writer_node
)


# Edges
workflow.add_edge(
    START,
    "manager_node"
)

workflow.add_conditional_edges(

    "manager_node",
    router_condition,

    {
        "search_node":
            "search_node",

        "fallback_node":
            "fallback_node"
    }
)

workflow.add_conditional_edges(

    "search_node",

    router_condition,

    {

        "writer":
            "writer_node",

        "fallback_node":
            "fallback_node"
    }
)

workflow.add_edge(
    "fallback_node",
    "writer_node"
)

workflow.add_edge(
    "writer_node",
    END
)


# =========================================================
# COMPILE
# =========================================================

app = workflow.compile()


# =========================================================
# INITIAL STATE
# =========================================================

initial_state = {

    "query": "In which field we use mostly java as our primary language",
    "route": "",
    "error_status": False,
    "fallback_raised": False,
    "message": [],
    "local_db_path": None,
    "data": "",
    "final_res": ""
}


# =========================================================
# EXECUTION
# =========================================================

logger.info(
    "Workflow execution started"
)

result = app.invoke(initial_state)

logger.info(
    "Workflow execution completed"
)

logger.info(
    "Final response generated successfully"
)

print(result.get("final_res"))
