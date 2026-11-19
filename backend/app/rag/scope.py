"""Requests outside the assistant's contract, answered by the backend before retrieval (ADR 0014).

The assistant answers questions about the merchant's PayFlow payments and the PayFlow documentation. Two kinds of
request are outside that contract and led Qwen to invent business facts (v3 q140: a marketing e-mail with an
invented discount code; q142: pricing advice):

- writing promotional or marketing content (an e-mail, newsletter, post, letter, campaign, slogan…);
- advice or forecasts about prices and sales ("should I raise the price?", "would I sell less?").

Data requests that share the words are not affected: "scrivimi l'email del cliente" asks for a restricted
attribute (handled by the structured route), not for a text to write.
"""

import re

_WRITE = r"\b(?:write|draft|compose|generate|prepare|scrivi\w*|redigi\w*|componi\w*|prepara\w*|genera\w*)\b"
_CONTENT = (
    r"(?:e-?mail|mail|newsletter|post|tweet|lettera|letter|annunci\w*|slogan|campagn\w*|campaign|promozion\w*"
    r"|promotional|marketing|pubblicit\w*|advert\w*)"
)
_GENERATION = re.compile(rf"{_WRITE}(?:\W+\w+){{0,6}}?\W+{_CONTENT}\b")
# "l'email del cliente", "the customer's email": a data request, not content to write.
_DATA_REQUEST = re.compile(
    r"\be-?mail\s+(?:del|dell'|della|di|of)\s*(?:the\s+)?(?:client\w*|customer|acquirent\w*|buyer|utente|user)"
    r"|\b(?:customer|client|buyer|user)(?:'s)?\s+e-?mail\b"
)
_ADVICE = re.compile(
    r"\b(?:should i|shall i|dovrei|mi conviene|conviene|consigli\w*|recommend\w*)\b[^?.]{0,50}"
    r"\b(?:price|prices|pricing|prezz\w+|raise|lower|alzar\w*|abbassar\w*|aumentar\w*|ridurr\w*)\b"
    r"|\b(?:would|will) i\s+sell\b|\bsell (?:more|less)\b|\bvender(?:ei|ò|o)\s+(?:di\s+)?(?:più|meno)\b"
    r"|\b(?:forecast|previsioni?)\b"
)

OUT_OF_SCOPE = {
    "it": (
        "Non posso aiutarti con questa richiesta: rispondo solo su pagamenti e documentazione PayFlow. Non scrivo "
        "testi promozionali, non do consigli su "
        "prezzi o vendite e non invento sconti o promozioni. Posso dirti, per esempio, quante unità di un prodotto "
        "hai venduto o quanto hai incassato."
    ),
    "en": (
        "I can't help with this request: I only answer about PayFlow payments and documentation. I do not write "
        "promotional content, give pricing "
        "or sales advice, or make up discounts or promotions. I can tell you, for example, how many units of a "
        "product you sold or how much you collected."
    ),
}


def out_of_scope_request(question: str) -> bool:
    text = question.lower().replace("’", "'")
    if _ADVICE.search(text):
        return True
    return bool(_GENERATION.search(text)) and not _DATA_REQUEST.search(text)
