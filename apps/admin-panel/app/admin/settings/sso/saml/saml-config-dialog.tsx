"use client";

/**
 * Alta y edición de la configuración SAML.
 *
 * Tres reglas que no se ven mirando el render y que fija `saml-config.test.tsx`:
 *
 *  - al EDITAR, la clave privada del SP vacía significa «conserva la guardada»;
 *  - `attribute_mappings` omite lo vacío (mandar `email: ""` haría al backend
 *    buscar un atributo sin nombre en la aserción);
 *  - un alta nace con `want_assertions_signed: true`.
 */

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch } from "@/lib/api";

import {
  DEFAULT_NAME_ID,
  NAME_ID_FORMATS,
  type FormState,
  type ParsedIdpMetadata,
  type SamlConfig,
  type UpsertBody,
} from "./saml-types";

function configToForm(config: SamlConfig | null): FormState {
  if (config === null) {
    return {
      display_name: "",
      idp_entity_id: "",
      idp_sso_url: "",
      idp_x509_cert: "",
      name_id_format: DEFAULT_NAME_ID,
      attribute_email: "",
      attribute_full_name: "",
      sp_x509_cert: "",
      sp_private_key: "",
      authn_requests_signed: false,
      want_assertions_signed: true,
      want_assertions_encrypted: false,
      want_name_id_encrypted: false,
      enabled: false,
    };
  }
  return {
    display_name: config.display_name ?? "",
    idp_entity_id: config.idp_entity_id,
    idp_sso_url: config.idp_sso_url,
    idp_x509_cert: config.idp_x509_cert,
    name_id_format: config.name_id_format,
    attribute_email: config.attribute_mappings.email ?? "",
    attribute_full_name: config.attribute_mappings.full_name ?? "",
    sp_x509_cert: config.sp_x509_cert ?? "",
    sp_private_key: "",
    authn_requests_signed: config.authn_requests_signed,
    want_assertions_signed: config.want_assertions_signed,
    want_assertions_encrypted: config.want_assertions_encrypted,
    want_name_id_encrypted: config.want_name_id_encrypted,
    enabled: config.enabled,
  };
}

export function SamlConfigDialog({
  open,
  onOpenChange,
  initial,
  submitting,
  onSubmit,
  backendError,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  initial: SamlConfig | null;
  submitting: boolean;
  onSubmit: (body: UpsertBody) => void;
  backendError: string | null;
}) {
  const isCreate = initial === null;
  const [state, setState] = useState<FormState>(() => configToForm(initial));
  const [metadataXml, setMetadataXml] = useState("");

  useEffect(() => {
    setState(configToForm(initial));
    setMetadataXml("");
  }, [initial]);

  const parseMutation = useMutation({
    mutationFn: (xml: string) =>
      apiFetch<ParsedIdpMetadata>("/auth/sso/saml/parse-metadata", {
        method: "POST",
        body: { metadata_xml: xml },
      }),
    onSuccess: (parsed) => {
      setState((s) => ({
        ...s,
        idp_entity_id: parsed.entity_id || s.idp_entity_id,
        idp_sso_url: parsed.sso_url || s.idp_sso_url,
        idp_x509_cert: parsed.x509_cert || s.idp_x509_cert,
        name_id_format: parsed.name_id_format || s.name_id_format,
      }));
    },
  });

  function buildBody(): UpsertBody {
    const attribute_mappings: Record<string, string> = {};
    if (state.attribute_email.trim()) attribute_mappings.email = state.attribute_email.trim();
    if (state.attribute_full_name.trim())
      attribute_mappings.full_name = state.attribute_full_name.trim();
    const body: UpsertBody = {
      display_name: state.display_name.trim() || null,
      enabled: state.enabled,
      idp_entity_id: state.idp_entity_id.trim(),
      idp_sso_url: state.idp_sso_url.trim(),
      idp_x509_cert: state.idp_x509_cert.trim(),
      name_id_format: state.name_id_format,
      attribute_mappings,
      sp_x509_cert: state.sp_x509_cert.trim() || null,
      authn_requests_signed: state.authn_requests_signed,
      want_assertions_signed: state.want_assertions_signed,
      want_assertions_encrypted: state.want_assertions_encrypted,
      want_name_id_encrypted: state.want_name_id_encrypted,
    };
    // Only send the SP key when the operator typed one. On edit, an empty
    // field means "keep the stored key".
    if (state.sp_private_key.trim()) {
      body.sp_private_key = state.sp_private_key;
    }
    return body;
  }

  const canSubmit =
    state.idp_entity_id.trim() !== "" &&
    state.idp_sso_url.trim() !== "" &&
    state.idp_x509_cert.trim() !== "";

  const parseError =
    parseMutation.isError && parseMutation.error instanceof ApiError
      ? parseMutation.error.body
      : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="saml-config-dialog">
        <DialogHeader>
          <DialogTitle>{isCreate ? "Configurar SAML" : "Editar configuración SAML"}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {/* IdP metadata upload / paste */}
            <div className="bg-muted/30 -mx-2 rounded-md border p-3">
              <Label htmlFor="saml-form-metadata">Metadatos del IdP (XML)</Label>
              <textarea
                id="saml-form-metadata"
                data-testid="saml-form-metadata"
                className="border-input bg-background mt-1 h-28 w-full resize-y rounded-md border px-3 py-2 font-mono text-xs"
                value={metadataXml}
                onChange={(e) => setMetadataXml(e.target.value)}
                placeholder="Pega aquí el EntityDescriptor del IdP, o sube el archivo de metadatos…"
              />
              <div className="mt-2 flex items-center gap-2">
                <input
                  id="saml-form-metadata-file"
                  data-testid="saml-form-metadata-file"
                  type="file"
                  accept=".xml,text/xml,application/xml"
                  className="hidden"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    const text = await file.text();
                    setMetadataXml(text);
                  }}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  data-testid="saml-form-metadata-upload"
                  onClick={() => document.getElementById("saml-form-metadata-file")?.click()}
                >
                  <Upload className="mr-1 h-3.5 w-3.5" />
                  Subir XML
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  data-testid="saml-form-metadata-parse"
                  disabled={metadataXml.trim() === "" || parseMutation.isPending}
                  onClick={() => parseMutation.mutate(metadataXml)}
                >
                  {parseMutation.isPending ? "Analizando…" : "Extraer datos"}
                </Button>
              </div>
              <p className="text-muted-foreground mt-1.5 text-xs">
                Extrae automáticamente Entity ID, URL de SSO y certificado del IdP. Después puedes
                ajustarlos manualmente.
              </p>
              {parseError ? (
                <p className="text-destructive mt-2 text-xs" data-testid="saml-form-parse-error">
                  No se pudieron extraer los metadatos: {parseError}
                </p>
              ) : null}
            </div>

            {/* Display name */}
            <div>
              <Label htmlFor="saml-form-display-name">Nombre visible (opcional)</Label>
              <Input
                id="saml-form-display-name"
                data-testid="saml-form-display-name"
                value={state.display_name}
                onChange={(e) => setState({ ...state, display_name: e.target.value })}
                placeholder="Acme Okta"
              />
            </div>

            {/* IdP Entity ID */}
            <div>
              <Label htmlFor="saml-form-entity-id">IdP Entity ID</Label>
              <Input
                id="saml-form-entity-id"
                data-testid="saml-form-entity-id"
                value={state.idp_entity_id}
                onChange={(e) => setState({ ...state, idp_entity_id: e.target.value })}
                placeholder="https://idp.example.com/saml/metadata"
              />
            </div>

            {/* IdP SSO URL */}
            <div>
              <Label htmlFor="saml-form-sso-url">URL de SSO del IdP</Label>
              <Input
                id="saml-form-sso-url"
                data-testid="saml-form-sso-url"
                value={state.idp_sso_url}
                onChange={(e) => setState({ ...state, idp_sso_url: e.target.value })}
                placeholder="https://idp.example.com/saml/sso"
              />
            </div>

            {/* IdP signing cert */}
            <div>
              <Label htmlFor="saml-form-cert">Certificado de firma del IdP (X.509)</Label>
              <textarea
                id="saml-form-cert"
                data-testid="saml-form-cert"
                className="border-input bg-background mt-1 h-24 w-full resize-y rounded-md border px-3 py-2 font-mono text-xs"
                value={state.idp_x509_cert}
                onChange={(e) => setState({ ...state, idp_x509_cert: e.target.value })}
                placeholder="MIID… (cuerpo base64 del certificado o PEM completo)"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                Con este certificado se verifica la firma de las aserciones del IdP.
              </p>
            </div>

            {/* NameID format */}
            <div>
              <Label htmlFor="saml-form-name-id">Formato de NameID</Label>
              <select
                id="saml-form-name-id"
                data-testid="saml-form-name-id"
                className="border-input bg-background mt-1 h-10 w-full rounded-md border px-3 text-sm"
                value={state.name_id_format}
                onChange={(e) => setState({ ...state, name_id_format: e.target.value })}
              >
                {NAME_ID_FORMATS.map((fmt) => (
                  <option key={fmt.value} value={fmt.value}>
                    {fmt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Attribute mappings */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="saml-form-attr-email">Atributo de email (opcional)</Label>
                <Input
                  id="saml-form-attr-email"
                  data-testid="saml-form-attr-email"
                  value={state.attribute_email}
                  onChange={(e) => setState({ ...state, attribute_email: e.target.value })}
                  placeholder="email"
                />
              </div>
              <div>
                <Label htmlFor="saml-form-attr-full-name">Atributo de nombre (opcional)</Label>
                <Input
                  id="saml-form-attr-full-name"
                  data-testid="saml-form-attr-full-name"
                  value={state.attribute_full_name}
                  onChange={(e) => setState({ ...state, attribute_full_name: e.target.value })}
                  placeholder="displayName"
                />
              </div>
            </div>

            {/* SP cert + private key (optional, for signing/encryption) */}
            <div className="space-y-3 rounded-md border p-3">
              <p className="text-muted-foreground text-xs">
                Clave del SP — solo necesaria si firmas el AuthnRequest o cifras las aserciones.
              </p>
              <div>
                <Label htmlFor="saml-form-sp-cert">Certificado público del SP (X.509)</Label>
                <textarea
                  id="saml-form-sp-cert"
                  data-testid="saml-form-sp-cert"
                  className="border-input bg-background mt-1 h-20 w-full resize-y rounded-md border px-3 py-2 font-mono text-xs"
                  value={state.sp_x509_cert}
                  onChange={(e) => setState({ ...state, sp_x509_cert: e.target.value })}
                  placeholder="MIID… (certificado público del SP)"
                />
              </div>
              <div>
                <Label htmlFor="saml-form-sp-key">
                  Clave privada del SP (PEM)
                  {isCreate ? "" : " (dejar vacío para conservar la actual)"}
                </Label>
                <textarea
                  id="saml-form-sp-key"
                  data-testid="saml-form-sp-key"
                  className="border-input bg-background mt-1 h-20 w-full resize-y rounded-md border px-3 py-2 font-mono text-xs"
                  value={state.sp_private_key}
                  onChange={(e) => setState({ ...state, sp_private_key: e.target.value })}
                  placeholder={
                    isCreate ? "Pega aquí la clave privada del SP en formato PEM…" : "••••••••"
                  }
                />
                <p className="text-muted-foreground mt-1 text-xs">
                  Se cifra en reposo antes de guardarse; el sistema nunca la devuelve en claro.
                </p>
              </div>
            </div>

            {/* Security flags */}
            <div className="space-y-2">
              <CheckboxRow
                id="saml-form-authn-signed"
                checked={state.authn_requests_signed}
                onChange={(v) => setState({ ...state, authn_requests_signed: v })}
                label="Firmar el AuthnRequest saliente (requiere clave del SP)"
              />
              <CheckboxRow
                id="saml-form-want-assertions-signed"
                checked={state.want_assertions_signed}
                onChange={(v) => setState({ ...state, want_assertions_signed: v })}
                label="Exigir aserciones firmadas por el IdP (recomendado)"
              />
              <CheckboxRow
                id="saml-form-want-assertions-encrypted"
                checked={state.want_assertions_encrypted}
                onChange={(v) => setState({ ...state, want_assertions_encrypted: v })}
                label="Exigir aserciones cifradas (requiere clave del SP)"
              />
              <CheckboxRow
                id="saml-form-want-name-id-encrypted"
                checked={state.want_name_id_encrypted}
                onChange={(v) => setState({ ...state, want_name_id_encrypted: v })}
                label="Exigir NameID cifrado (requiere clave del SP)"
              />
            </div>

            {/* Enabled */}
            <CheckboxRow
              id="saml-form-enabled"
              checked={state.enabled}
              onChange={(v) => setState({ ...state, enabled: v })}
              label="Activar este proveedor en el login (añadido al login local y a OIDC, no los reemplaza)"
            />

            {backendError ? (
              <p
                className="text-destructive whitespace-pre-wrap text-xs"
                data-testid="saml-form-backend-error"
              >
                {backendError}
              </p>
            ) : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
            data-testid="saml-form-cancel"
          >
            Cancelar
          </Button>
          <Button
            onClick={() => onSubmit(buildBody())}
            disabled={submitting || !canSubmit}
            data-testid="saml-form-submit"
          >
            {submitting ? "Guardando…" : isCreate ? "Crear" : "Guardar cambios"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CheckboxRow({
  id,
  checked,
  onChange,
  label,
}: {
  id: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex items-center gap-2 text-sm" htmlFor={id}>
      <input
        id={id}
        data-testid={id}
        type="checkbox"
        className="h-4 w-4 rounded border"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}
