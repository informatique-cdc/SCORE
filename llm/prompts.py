"""
Prompt templates for LLM-based analysis tasks.

All prompts are defined here to keep them maintainable and auditable.
"""

CLAIM_EXTRACTION = """\
Extrais les affirmations factuelles atomiques du passage de texte suivant.
Pour chaque affirmation, fournis un objet JSON structuré avec :
- subject : l'entité ou le sujet
- predicate : la relation ou l'action
- object : la valeur, l'état ou la cible
- qualifiers : toute condition, date, version ou limitation de portée (sous forme de dict)
- date : la date à laquelle l'affirmation se rapporte (format ISO, ou null si inconnue)
- raw_text : la citation exacte du passage étayant cette affirmation

Renvoie un tableau JSON d'affirmations. Extrais au maximum {max_claims} affirmations.
N'extrais que les affirmations factuelles et vérifiables — ignore les opinions, les questions et les instructions.

Passage de texte :
---
{text}
---

Réponds avec un objet JSON : {{"claims": [...]}}"""

CONTRADICTION_CHECK = """\
Tu es un expert en vérification des faits. Compare ces deux affirmations et détermine leur relation.

Affirmation A (de « {doc_a_title} », {doc_a_date}) :
« {claim_a} »

Affirmation B (de « {doc_b_title} », {doc_b_date}) :
« {claim_b} »

Contexte de l'affirmation A :
« {context_a} »

Contexte de l'affirmation B :
« {context_b} »

Classe la relation comme exactement l'une des suivantes :
- "entailment" : L'affirmation B soutient ou est cohérente avec l'affirmation A
- "contradiction" : L'affirmation B contredit l'affirmation A sur le même sujet
- "outdated" : L'une des affirmations est une version plus récente qui remplace l'autre (précise laquelle est la plus récente)
- "unrelated" : Les affirmations portent sur des sujets différents

Réponds avec un JSON :
{{
  "classification": "entailment|contradiction|outdated|unrelated",
  "confidence": 0.0-1.0,
  "evidence": "Explication détaillée du choix de cette classification, citant des parties spécifiques de chaque affirmation.",
  "newer_claim": "A" or "B" or null (uniquement pour outdated),
  "severity": "high|medium|low"
}}"""

DUPLICATE_VERIFICATION = """\
Tu es un analyste documentaire. Détermine si ces deux documents sont des doublons, liés, ou sans rapport.

Document A : « {title_a} »
Chemin : {path_a}
Extrait :
---
{excerpt_a}
---

Document B : « {title_b} »
Chemin : {path_b}
Extrait :
---
{excerpt_b}
---

Scores de similarité déjà calculés :
- Similarité sémantique : {semantic_score:.3f}
- Similarité lexicale : {lexical_score:.3f}
- Similarité des métadonnées : {metadata_score:.3f}

Classe comme :
- "duplicate" : Même contenu, l'un devrait être supprimé ou fusionné
- "related" : Même sujet mais contenu/perspective différent — conserver les deux
- "unrelated" : Sujets différents, signalé à tort

Réponds avec un JSON :
{{
  "classification": "duplicate|related|unrelated",
  "confidence": 0.0-1.0,
  "evidence": "Explication de ton raisonnement",
  "recommended_action": "merge|delete_older|keep_both|review"
}}"""

CLUSTER_SUMMARY = """\
Tu es un analyste de base de connaissances. Analyse les extraits de documents suivants qui \
ont été regroupés par similarité sémantique.

Ta tâche :
1. Détermine le SUJET spécifique que ces documents partagent. Utilise un libellé clair et descriptif \
   (2 à 5 mots) qu'un humain utiliserait pour nommer cette catégorie dans une table des \
   matières. Sois précis — évite les libellés vagues comme « Informations générales » ou \
   « Documentation générale ».
2. Rédige un résumé de 2 à 3 phrases des connaissances couvertes.
3. Liste les concepts clés mentionnés dans l'ensemble des documents.
4. En une phrase, décris l'objectif principal de ce contenu (à quoi sert-il pour les utilisateurs).

Extraits de documents :
{excerpts}

Réponds avec un JSON :
{{
  "label": "...",
  "summary": "...",
  "key_concepts": ["concept1", "concept2", ...],
  "content_purpose": "Une phrase décrivant l'objectif de ce contenu"
}}"""

TOPIC_TAXONOMY = """\
Tu es un architecte de base de connaissances. À partir de ces clusters thématiques découverts dans un \
référentiel documentaire, organise-les en une taxonomie hiérarchique claire.

Crée une hiérarchie à 2 niveaux :
- Niveau 1 : Catégories larges qui regroupent les clusters apparentés \
  (ex. : « Infrastructure », « Documentation produit », « Politiques et conformité »)
- Niveau 2 : Les clusters eux-mêmes, assignés à la catégorie la plus appropriée

Règles :
- Utilise des noms de catégories clairs et professionnels (2 à 4 mots)
- Chaque cluster doit apparaître dans exactement une catégorie
- Crée 2 à 5 catégories de premier niveau (moins c'est mieux — ne sépare que lorsque les sujets \
  relèvent véritablement de domaines différents)
- Si tous les clusters appartiennent à un seul domaine, utilise une seule catégorie
- Ordonne les catégories et les clusters de manière logique (les plus importants en premier)

Clusters :
{cluster_list}

Réponds avec un JSON :
{{
  "taxonomy": [
    {{
      "category": "Nom de la catégorie",
      "clusters": [0, 2, 5]
    }},
    ...
  ]
}}

Les nombres dans "clusters" sont les indices des clusters de la liste ci-dessus (indexés à partir de 0)."""

CHAT_TITLE_SYSTEM = """\
Génère un titre court (5 mots maximum) pour cette conversation de chat. \
Le titre doit capturer le sujet principal de la question de l'utilisateur. \
Réponds uniquement avec le titre, sans guillemets ni ponctuation finale."""

CHAT_QA_SYSTEM = """\
Tu es un assistant documentaire pour la plateforme SCORE. Tu réponds aux questions \
des utilisateurs en te basant UNIQUEMENT sur les passages de documents fournis ci-dessous.

Règles :
- Réponds de manière claire, structurée et concise.
- Cite tes sources en mentionnant le titre du document entre crochets, ex. [Nom du document].
- Si l'information demandée n'est pas présente dans les passages fournis, dis-le clairement : \
  « Je n'ai pas trouvé cette information dans les documents disponibles. »
- Ne fabrique jamais d'information. Ne fais pas de suppositions au-delà de ce qui est écrit.
- Tu peux reformuler et synthétiser, mais reste fidèle au contenu source.
- À la fin de ta réponse, suggère 3 questions de suivi pertinentes que l'utilisateur pourrait poser, \
  préfixées par « >> ». Exemple : >> Quels sont les délais de livraison mentionnés ?

Passages de documents :
---
{context}
---"""

DUPLICATE_VERIFICATION_BATCH = """\
Tu es un analyste documentaire. Pour chaque paire de documents ci-dessous, détermine s'ils sont des doublons, liés, ou sans rapport.

{pairs_block}

Pour CHAQUE paire, classe comme :
- "duplicate" : Même contenu, l'un devrait être supprimé ou fusionné
- "related" : Même sujet mais contenu/perspective différent — conserver les deux
- "unrelated" : Sujets différents, signalé à tort

Réponds avec un JSON :
{{
  "results": [
    {{
      "pair_index": 0,
      "classification": "duplicate|related|unrelated",
      "confidence": 0.0-1.0,
      "evidence": "Explication courte",
      "recommended_action": "merge|delete_older|keep_both|review"
    }},
    ...
  ]
}}"""

KG_TRIPLE_EXTRACTION = """\
Lis le texte ci-dessous et identifie toutes les relations Sujet-Prédicat-Objet qu'il exprime.

Règles impératives :
- Termes atomiques : chaque sujet et chaque objet doit être une entité courte et indivisible \
(personne, organisation, produit, lieu, concept, dispositif, date, montant). \
Ne fusionne jamais plusieurs idées dans un même terme, et n'y mets pas de phrase.
- Prédicats de 1 à 3 mots maximum. Jamais plus de 3 mots.
- Cohérence : désigne une même entité par une même chaîne partout. \
Si le texte alterne « le RGPD », « RGPD » et « règlement général sur la protection des données », \
retiens la forme la plus courante et utilise-la systématiquement.
- Remplace les pronoms (« il », « celle-ci », « ce dernier »…) par l'entité qu'ils désignent.
- Relie par paires : si plusieurs termes d'une même phrase entretiennent une relation \
explicite, produis un triplet pour chaque paire concernée.
- N'invente rien : chaque triplet doit être justifiable par le texte.

Extrais au maximum {max_triples} triplets.

Texte à analyser :
---
{text}
---

Réponds avec un objet JSON : {{"triples": [{{"subject": "...", "predicate": "...", "object": "..."}}]}}"""

KG_CROSS_CLUSTER_INFERENCE = """\
Deux ensembles d'entités proviennent de thématiques distinctes du même corpus documentaire.
Ils ne sont reliés par aucune relation connue.

Thématique A — « {cluster_a_label} »
Entités : {entities_a}

Thématique B — « {cluster_b_label} »
Entités : {entities_b}

Relations déjà constatées dans le corpus, pour te situer :
{existing_relations}

Identifie au maximum {max_relations} relations qui existent **de façon manifeste** entre une entité de A \
et une entité de B, au vu de ce que ces deux thématiques recouvrent.

Règles impératives :
- **Ne force rien.** S'il n'existe aucune relation manifeste, renvoie une liste vide. \
Une liste vide est une réponse correcte et attendue : deux thématiques peuvent légitimement \
n'avoir aucun lien.
- N'invente aucune entité : sujet et objet doivent figurer mot pour mot dans les listes ci-dessus.
- Le sujet doit venir de A et l'objet de B.
- Sujet et objet doivent être différents.
- Prédicats de 1 à 3 mots maximum.
- Une relation vague du type « est lié à » n'apporte rien : ne la propose pas.
- Indique pour chaque relation une confiance entre 0 et 1. En dessous de 0,7, ne la propose pas.

Réponds avec un objet JSON : \
{{"relations": [{{"subject": "...", "predicate": "...", "object": "...", "confidence": 0.0}}]}}"""

KG_WITHIN_CLUSTER_INFERENCE = """\
Les paires d'entités suivantes appartiennent à la même thématique — « {cluster_label} » — \
mais ne sont reliées par aucune relation connue.

Paires à examiner :
{pairs}

Relations déjà constatées dans le corpus, pour te situer :
{existing_relations}

Pour chaque paire, indique la relation qui les unit **si elle est manifeste**.

Règles impératives :
- **Ne force rien.** Écarte toute paire dont la relation ne serait qu'une conjecture. \
Renvoyer une liste vide est une réponse correcte.
- N'invente aucune entité : sujet et objet doivent figurer mot pour mot dans les paires ci-dessus.
- Sujet et objet doivent être différents.
- Prédicats de 1 à 3 mots maximum.
- Une relation vague du type « est lié à » n'apporte rien : ne la propose pas.
- Indique pour chaque relation une confiance entre 0 et 1. En dessous de 0,7, ne la propose pas.

Réponds avec un objet JSON : \
{{"relations": [{{"subject": "...", "predicate": "...", "object": "...", "confidence": 0.0}}]}}"""

CONCEPT_CONTEXT = """\
Contexte conceptuel issu du graphe sémantique :
Les concepts suivants sont liés à la question de l'utilisateur, avec leurs relations.

Concepts principaux : {seed_concepts}

Relations :
{relationships}

Utilise ce contexte conceptuel en complément des passages documentaires pour fournir \
une réponse plus complète et structurée."""

GAP_DETECTION_QUESTIONS = """\
À partir de ce cluster thématique et de son résumé, génère {n_questions} questions auxquelles
un ensemble de documentation complet sur ce sujet DEVRAIT pouvoir répondre.
Concentre-toi sur les questions pratiques et importantes auxquelles les utilisateurs auraient besoin de réponses.

Cluster : {cluster_label}
Résumé : {cluster_summary}
Concepts clés : {key_concepts}

Clusters associés : {adjacent_clusters}

Réponds avec un JSON :
{{
  "questions": [
    {{"question": "...", "importance": "high|medium|low"}},
    ...
  ]
}}"""

GAP_COVERAGE_CHECK = """\
À partir de cette question et des passages récupérés, évalue si la documentation
répond de manière adéquate à la question.

Question : {question}

Passages récupérés :
{passages}

Réponds avec un JSON :
{{
  "answered": true/false,
  "confidence": 0.0-1.0,
  "explanation": "Pourquoi cette question est ou n'est pas traitée par la documentation",
  "missing_info": "Quelle information devrait être ajoutée pour répondre à cette question" (uniquement si non traitée)
}}"""
