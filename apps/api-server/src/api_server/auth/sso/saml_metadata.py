"""Parse SAML 2.0 IdP metadata XML (Plan 08 task_08_06).

The SAML config UI lets a tenant-admin paste/upload their IdP's
``EntityDescriptor`` metadata document; this module extracts the three
values the config needs — the IdP ``entityID``, the HTTP-Redirect
single-sign-on URL, and the signing certificate — plus an optional
preferred ``NameIDFormat``.

It uses ``lxml`` with a HARDENED parser (no DTD load, no network, no
entity resolution) — this is the same safe-XML stance the rest of the
platform takes. Crucially it needs NO native ``xmlsec`` backend: it only
reads the XML, it does not verify any signature. So the parse endpoint
works on every node, even one where ``python3-saml``/``xmlsec`` could not
be installed (the assertion-verification path is the only thing that
truly needs the native crypto).
"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree  # type: ignore[import-untyped]

# SAML 2.0 metadata + XML-DSig namespaces.
_NS = {
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}
_HTTP_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
_HTTP_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


class IdPMetadataError(Exception):
    """The supplied IdP metadata XML could not be parsed.

    Client-attributable (the operator pasted something that is not a
    valid SAML ``EntityDescriptor``) — the router maps it to a 422.
    """


@dataclass(frozen=True)
class ParsedIdPMetadata:
    """The fields lifted out of an IdP metadata document.

    ``sso_url`` / ``x509_cert`` may be empty strings when the document
    omits an HTTP-Redirect SSO binding or a signing certificate; the
    caller then asks the operator to supply them manually.
    """

    entity_id: str
    sso_url: str
    x509_cert: str
    name_id_format: str | None


def _safe_parser() -> etree.XMLParser:
    """An lxml parser hardened against XXE / entity-expansion / SSRF.

    ``resolve_entities=False`` defuses entity expansion (billion-laughs),
    ``no_network=True`` blocks external fetches, ``load_dtd=False`` and
    ``dtd_validation=False`` keep any inline/loaded DTD inert. Metadata
    documents are plain XML with no legitimate need for any of those.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def parse_idp_metadata(metadata_xml: str) -> ParsedIdPMetadata:
    """Extract the IdP config fields from a metadata ``EntityDescriptor``.

    Picks the IDPSSODescriptor's HTTP-Redirect SSO endpoint (the binding
    the SP-initiated flow uses), the first signing certificate, and the
    entity id. The certificate is returned as the raw base64 body with
    whitespace collapsed — exactly what ``python3-saml`` wants in its
    ``idp.x509cert`` setting.

    Raises:
        IdPMetadataError: the document is not well-formed XML, is not a
            SAML ``EntityDescriptor``, or has no ``entityID``.
    """
    text = metadata_xml.strip()
    if not text:
        raise IdPMetadataError("empty metadata document")
    try:
        # Parse from bytes so an XML declaration with an encoding is honored.
        root = etree.fromstring(text.encode("utf-8"), parser=_safe_parser())
    except etree.XMLSyntaxError as exc:
        raise IdPMetadataError(f"metadata is not well-formed XML: {exc}") from exc

    # The document root may itself be the EntityDescriptor, or an
    # EntitiesDescriptor wrapping one or more of them — take the first
    # EntityDescriptor that carries an IDPSSODescriptor.
    entity = _find_idp_entity(root)
    if entity is None:
        raise IdPMetadataError("metadata has no <EntityDescriptor> with an <IDPSSODescriptor>")

    entity_id = entity.get("entityID", "").strip()
    if not entity_id:
        raise IdPMetadataError("metadata <EntityDescriptor> has no entityID")

    idp = entity.find("md:IDPSSODescriptor", _NS)
    # _find_idp_entity guarantees an IDPSSODescriptor is present.
    assert idp is not None  # - invariant from _find_idp_entity

    sso_url = _first_sso_url(idp)
    x509_cert = _first_signing_cert(idp)
    name_id_format = _first_name_id_format(idp)

    return ParsedIdPMetadata(
        entity_id=entity_id,
        sso_url=sso_url,
        x509_cert=x509_cert,
        name_id_format=name_id_format,
    )


def _find_idp_entity(root: etree._Element) -> etree._Element | None:
    """Locate the EntityDescriptor that describes an IdP."""
    tag = etree.QName(root).localname
    if tag == "EntityDescriptor":
        if root.find("md:IDPSSODescriptor", _NS) is not None:
            return root
        return None
    # EntitiesDescriptor (or anything wrapping descriptors): scan children.
    for entity in root.findall(".//md:EntityDescriptor", _NS):
        if entity.find("md:IDPSSODescriptor", _NS) is not None:
            return entity
    return None


def _first_sso_url(idp: etree._Element) -> str:
    """The HTTP-Redirect SingleSignOnService Location, else HTTP-POST, else any."""
    services = idp.findall("md:SingleSignOnService", _NS)
    for binding in (_HTTP_REDIRECT, _HTTP_POST):
        for svc in services:
            if svc.get("Binding") == binding:
                location = (svc.get("Location") or "").strip()
                if location:
                    return location
    # Last resort: the first service with any binding.
    for svc in services:
        location = (svc.get("Location") or "").strip()
        if location:
            return location
    return ""


def _first_signing_cert(idp: etree._Element) -> str:
    """The first signing (or use-less) X509 certificate, base64, whitespace-stripped.

    SAML metadata may tag a KeyDescriptor ``use="signing"``,
    ``use="encryption"``, or leave ``use`` unset (then it serves both).
    We prefer a signing/unspecified key — that is what verifies the IdP's
    assertion.
    """
    key_descriptors = idp.findall("md:KeyDescriptor", _NS)
    # Prefer signing / unspecified; fall back to any cert present.
    for accept in (("signing", None), ("encryption",)):
        for kd in key_descriptors:
            use = kd.get("use")
            if use in accept or (None in accept and use is None):
                cert = kd.find("ds:KeyInfo/ds:X509Data/ds:X509Certificate", _NS)
                if cert is not None and cert.text:
                    return _collapse_cert(cert.text)
    return ""


def _first_name_id_format(idp: etree._Element) -> str | None:
    node = idp.find("md:NameIDFormat", _NS)
    if node is not None and node.text:
        stripped = str(node.text).strip()
        if stripped:
            return stripped
    return None


def _collapse_cert(raw: str) -> str:
    """Strip PEM headers/whitespace, leaving the bare base64 body."""
    lines = [
        line.strip()
        for line in raw.strip().splitlines()
        if line.strip() and not line.strip().startswith("-----")
    ]
    if lines:
        return "".join(lines)
    # Single-line / no-newline body: drop any embedded whitespace.
    return "".join(raw.split())


__all__ = [
    "IdPMetadataError",
    "ParsedIdPMetadata",
    "parse_idp_metadata",
]
