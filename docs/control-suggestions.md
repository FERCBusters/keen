# Evidence control suggestions

The mapping builder uses the mapping name and, optionally, the selected event's
summary to suggest controls or clauses in the chosen enabled framework. Suggestions
use the current in-scope catalogue rows, including titles, descriptions,
justifications, guidance, objectives and keyword/tag values. Saved catalogue edits
are visible on the next suggestion request.

The mapping name has priority over sample text. Each result explains its literal
word matches and related concepts. For example, **Firewall activity** finds
ISO27001:2022 **A.8.20 Networks security** and **A.8.21 Security of network services**
through the network-protection vocabulary. **Package upgrades** finds **A.8.8
Management of technical vulnerabilities**. The same vocabulary can identify
relevant text in KEEN-AF and organisation-defined frameworks.

Click a suggestion to add it to the mapping's targets. Review whether the events
actually demonstrate the intended requirement, period, assets and scope. An event
about a blocked connection or failed check can be relevant evidence; a suggestion
does not assert that a control is effective or a requirement has been satisfied.

## Maintaining the vocabulary

`services/api/app/services/evidence_taxonomy.py` contains the curated concepts:

- `name` is the human-readable concept.
- `aliases` are phrases that can appear in a mapping name or sample.
- `targets` are related phrases that can appear in the catalogue. Target phrases
  can also activate their own concept when used in the mapping name.

Add specific phrases and a regression example in
`services/api/tests/test_control_suggestions.py`. Prefer “network security” to
“security”, and “physical entry” to “entry”. Phrase boundaries prevent `apt` from
matching `aptitude`. Common plural forms, punctuation and selected British/US
spellings are normalised. Related phrases are expanded one hop; recommendations
remain bounded and explainable. Repeating a word does not increase its score.

`services/api/app/services/control_suggestions.py` performs ranking. A title match
has more weight than a descriptive match. Generic words are excluded from literal
matching, and the number of synonyms in a concept does not multiply its weight.
The endpoint returns up to ten suggestions from at most 5,000 in-scope catalogue
rows. No external language model or network service is used. The existing KEEN
Mitigator analysis and its configurable risk rules are unchanged.

## Framework selection and risk templates

Saving the organisation framework selection immediately updates the current
page's navigation selector. If the active framework was disabled, the navigation
uses the saved organisation default and updates framework-aware links. The
Clauses link follows that framework's clause availability. Other already-open
pages use their current catalogue until refreshed.

The reusable risk library is available independently of KEEN-AF. Template cards
show their scenario and example ratings, without a count of suggested KEEN-AF
controls. The optional automatic KEEN-AF links are offered only when KEEN-AF is
enabled for the organisation and permitted by `KEEN_ENABLED_FRAMEWORKS`. The
backend checks this again when creating the risk, so a previously opened form
cannot implicitly add template links after the framework is disabled. Disabling a
framework does not delete earlier risk/control links or template contents.
