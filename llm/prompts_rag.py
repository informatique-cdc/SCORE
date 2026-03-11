"""
Prompt templates for RAG technique functions.

All prompts expect LLM JSON output unless noted otherwise.
"""

RAG_FUSION_VARIANTS = """\
Tu es un assistant de reformulation de requêtes. À partir de la question de l'utilisateur, \
génère {n_variants} variantes sémantiquement différentes mais qui visent la même information.

Question originale : {question}

Réponds avec un JSON :
{{"queries": ["variante 1", "variante 2", ...]}}"""

HYDE_HYPOTHETICAL = """\
Tu es un expert documentaire. À partir de la question de l'utilisateur, rédige un court \
passage (3 à 5 phrases) qui pourrait être extrait d'un document répondant à cette question. \
Ce passage doit être plausible, factuel et rédigé dans le style d'une documentation technique.

Question : {question}

Réponds uniquement avec le passage hypothétique, sans aucun préambule."""

DECOMPOSITION_SUBQUESTIONS = """\
Tu es un assistant d'analyse de questions. Décompose la question complexe suivante \
en {max_sub} sous-questions plus simples et autonomes. Chaque sous-question doit \
cibler un aspect spécifique de la question originale.

Si la question est déjà simple et ne nécessite pas de décomposition, renvoie-la telle quelle.

Question : {question}

Réponds avec un JSON :
{{"sub_questions": ["sous-question 1", "sous-question 2", ...]}}"""

DECOMPOSITION_SYNTHESIS = """\
Tu es un assistant documentaire. L'utilisateur a posé une question complexe qui a été \
décomposée en sous-questions. Voici les résultats de chaque sous-question.

Question originale : {question}

{sub_results_block}

Synthétise une réponse cohérente et complète à la question originale en combinant \
les informations de toutes les sous-réponses. Cite les sources entre crochets [Nom du document].

À la fin de ta réponse, suggère 3 questions de suivi pertinentes préfixées par « >> ».

Réponds directement avec la synthèse."""

RERANK_SCORE = """\
Tu es un juge de pertinence documentaire. Évalue la pertinence de chaque passage \
par rapport à la question de l'utilisateur. Attribue un score de 0 à 10.

Question : {question}

Passages :
{chunks_block}

Pour chaque passage, attribue un score de pertinence (0 = non pertinent, 10 = parfaitement pertinent) \
et explique brièvement ton raisonnement.

Réponds avec un JSON :
{{"scores": [{{"chunk_index": 0, "score": 8, "reason": "..."}}, ...]}}"""

CRAG_EVALUATE = """\
Tu es un évaluateur de qualité de récupération documentaire. Évalue si les passages \
récupérés sont suffisants pour répondre à la question de l'utilisateur.

Question : {question}

Passages récupérés :
{chunks_block}

Évalue la qualité globale des résultats :
- "good" : Les passages contiennent l'information nécessaire pour répondre à la question.
- "poor" : Les passages sont hors sujet ou insuffisants.

Si la qualité est "poor", propose une reformulation de la question qui pourrait donner \
de meilleurs résultats.

Réponds avec un JSON :
{{"quality": "good|poor", "confidence": 0.0-1.0, "suggested_reformulation": "..." ou null}}"""

SELF_RAG_FILTER = """\
Tu es un juge de pertinence documentaire. Pour chaque passage ci-dessous, \
détermine s'il est pertinent pour répondre à la question de l'utilisateur.

Question : {question}

Passages :
{chunks_block}

Pour chaque passage, indique s'il est pertinent (true) ou non (false).

Réponds avec un JSON :
{{"judgments": [{{"chunk_index": 0, "relevant": true}}, ...]}}"""

AGENTIC_PLAN = """\
Tu es un agent de recherche documentaire autonome. Tu dois répondre à la question \
de l'utilisateur en effectuant des recherches itératives dans la base documentaire.

Question : {question}

{scratchpad}

À chaque étape, choisis UNE action parmi :
- {{"action": "search", "query": "ta requête de recherche"}} — recherche sémantique dans les documents
- {{"action": "search_graph", "query": "ta requête"}} — recherche dans le graphe de concepts
- {{"action": "answer", "content": "ta réponse finale"}} — réponds à la question

Règles :
- Maximum {max_steps} étapes de recherche.
- Formule des requêtes de recherche précises et variées.
- Quand tu as suffisamment d'information, utilise l'action "answer".
- Dans ta réponse finale, cite les sources [Nom du document] et ajoute 3 suggestions « >> ».

Réponds avec un JSON contenant UNE seule action."""
