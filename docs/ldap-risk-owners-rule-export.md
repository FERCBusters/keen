# LDAP sign-in, role ownership and mapping exports

## Directory sign-in

Enable `KEEN_LDAP_ENABLED=true` and configure the LDAP settings in `.env.example`.
Keep `KEEN_LOCAL_AUTH_ENABLED=true` while verifying directory access so an existing local
administrator can still sign in. The login form offers LDAP directory and Local KEEN account
when both are enabled. LDAP passwords are checked by binding as the matched directory entry;
KEEN does not store them. Password changes happen in the directory.

Use `ldaps://ldap.example.org:636` for LDAPS or `ldap://ldap.example.org:389` for mandatory
StartTLS. Certificate and hostname verification are always enabled. Installations with a
private CA can set `KEEN_LDAP_CA_CERT_PATH=/app/config/ldap-ca.pem` and place that public PEM
CA certificate in `config/ldap-ca.pem` (the standard Compose file mounts config read-only).
Other paths require a read-only mount into the API container. Restart/recreate the API after
environment changes. Never put the directory service password in a committed config file.

`KEEN_LDAP_BASE_DN=ou=Engineering,dc=example,dc=org` limits lookup to that OU and its
subtree. Set a broader base to admit more OUs. `KEEN_LDAP_USER_FILTER` adds an administrator-
controlled LDAP filter, for example `(&(objectClass=person)(memberOf=cn=KEEN,ou=Groups,dc=example,dc=org))`
where the directory supports `memberOf`. The username attribute defaults to `uid`; Active
Directory commonly uses `sAMAccountName`. Email defaults to `mail`.

KEEN requires exactly one matching entry and a stable unique ID. Use `entryUUID` for OpenLDAP
or `objectGUID` for Active Directory. Missing or ambiguous attributes deny authentication.
Referrals are disabled. A read-only search account needs permission to read these attributes.
Both bind DN and password may be empty when the directory permits anonymous search.

A new LDAP identity receives a normal KEEN account, normally named `ldap:<username>`, with
permissions assigned through KEEN administration. Existing local usernames and matching email
addresses do not establish identity links. If a name is occupied, KEEN generates a unique name.
The directory's host, scheme and port identify the configured directory; changing them requires
an explicit identity migration to preserve account assignments. Changing the search OU does
not change identity, but users outside the new search subtree cannot sign in.

LDAP sign-ins use KEEN's MFA policy, TOTP, security keys and one-time recovery codes. Use the
LDAP password to begin managing MFA under Account → Security. Set up the existing MFA origin,
encryption key and HTTPS settings as for local sign-in. Login notifications use SMTP when
configured. `KEEN_LDAP_AUTO_PROVISION=false` admits only previously provisioned LDAP accounts.
Disabling a KEEN user blocks their directory login too. Directory deactivation or OU removal
is checked on the next password authentication; existing KEEN sessions expire according to
KEEN's session lifetime. Disable the KEEN account for immediate application access revocation.
Hosted owner-only demo mode does not allow LDAP.

## Exporting evidence mapping rules

Open Administration → Sources & Evidence Mapping → Browse existing mappings. Select a source
and choose **Export YAML for selected source**. All sources exports the complete rule set.
The text search does not limit downloads. Administrator permission is required. Export includes
disabled rules, IDs, descriptions, structured-field conditions, confidence, collection selectors
and framework targets. It contains mapping rules only; manage collection definitions and
connection credentials separately.

The download has a top-level `rules:` list compatible with `KEEN_RULES_PATH` (default
`/app/config/rules.yml`). Source exports select exact `when.source` matches. Cross-source regular-
expression rules are included in the All sources export. Merge the `rules` lists from multiple
source exports into one document and retain unique IDs. Preserve referenced collection IDs and
framework/control references in the receiving installation.

**Database-managed rules take precedence over the file.** Replacing rules.yml alone will not
change an installation that has saved rules through the UI. To move that installation back to
file management, first export All sources and back up the database, deploy the complete YAML,
then have an operator remove only the active override in a database transaction:

```sql
DELETE FROM managed_configurations WHERE name = 'rules';
```

Historical configuration revisions remain available. This operation changes the authoritative
rules for every ingester; use the complete export or intentionally merge all desired rules.
Saving a mapping through the UI creates a new database override. Existing evidence mappings
remain until explicitly reprocessed or removed through the normal mapping tools.

## Risk owners

A risk has one owner: a user, an organisational role, or no owner. Create organisational roles
in the ISMS org chart and assign their users there. The risk editor groups the owner choices
into roles and users. Directly assigned users of an owning role see the risk under My risks
and have the same risk-specific read/question access as an individual owner. Editing risks
continues to require `risk.manage`. Membership of a parent node does not confer ownership of
roles below it. Changes to role membership apply to the next access check.

The risk register, detail pages, control associations and change history show the role owner.
CSV exports include `owner_role_id` separately from `owner_username`; importing role assignments
requires the same role IDs in the destination. Deleting an owning organisational role leaves
the risk unassigned.

## Organisation framework selection

In Administration → Frameworks, select the frameworks your organisation uses and choose an
organisation default. At least one framework must remain selected. The regular catalogue,
navigation and mapping-builder selectors offer that selection. The framework editor retains
access to the full catalogue so administrators can edit and re-enable a framework later.

`KEEN_ENABLED_FRAMEWORKS=ISO27001:2022,KEEN-AF:1.0` sets an optional comma-separated upper
limit in `.env`. Leave it empty to allow all frameworks. The administration selection must fit
within that environment list. Unknown slugs appear as a configuration warning in Administration.
If an environment change excludes every saved selection, the available environment frameworks
become the selection until an administrator saves another choice. An environment list containing
only unknown slugs yields an empty catalogue; correct the configuration or create the framework.

Hidden frameworks retain their controls, audit records, evidence and existing mapping rules.
This is a workspace catalogue preference, not an access-control boundary: existing rules and
explicit API operations still function. Disable any mapping rules you no longer want to run.
Reload open pages after changing the selection. A previously selected unavailable framework
falls back to the organisation default in the navigation.

## Laptop navigation

The complete navbar expands from 992 CSS pixels, covering common laptop browser widths. Its
framework picker displays compact framework slugs, with full names in tooltips and an accessible
label. It uses slightly tighter spacing at laptop widths and can wrap when necessary. Smaller
viewports and sufficiently magnified layouts retain the mobile menu. Shared MOSP assets require
no patch.
