"""Fictional ISMS document library with control and clause links.

Every document is a short worked example with in-app content and a first
revision. Links name explicit references per framework; a framework the run
does not seed is left out. After the run links controls to the documents,
``seed_link_diary`` records each document's automatic diary entry exactly as
linking a control to a document does in the application.
"""

import hashlib
from datetime import datetime, time, timedelta
from html import escape

from app.db.models import Event, IsmsDocument, IsmsDocumentFolder, IsmsDocumentRevision
from app.security.diary_visibility import DIARY_SOURCE, link_entry_external_id
from app.security.rich_text import sanitize_rich_text_html
from app.services.isms.link_diary import sync_entity_link_diary

ISO, SOC2, PCI = "iso_27001_2022", "soc2_tsc", "pci_dss_v4"
NOTICE = "Fictional demonstration document. Adapt it to the organisation before use."

# key: title, type, folder, tags, description, days since the document was
# approved and linked, control references per framework, ISO clauses, and
# content sections as (heading, paragraph text or a list of points).
DOCUMENTS = {
    "security_policy": dict(
        title="Information Security Policy",
        type="policy",
        folder="Policies",
        tags=["policy", "board-approved"],
        description="Sets the organisation's ISMS principles, roles, and control expectations.",
        linked=120,
        controls={
            ISO: ["A.5.1", "A.5.2", "A.5.4"],
            SOC2: ["CC1.1", "CC5.3"],
            PCI: ["12.1"],
        },
        clauses=["5.1", "5.2"],
        sections=[
            (
                "Purpose",
                "This policy sets out how the organisation protects the confidentiality, integrity and availability of the information it holds for customers, staff and partners.",
            ),
            (
                "Principles",
                [
                    "Information security is managed through a risk-based ISMS aligned to ISO/IEC 27001:2022.",
                    "Access is granted on least privilege and reviewed regularly.",
                    "Security events are reported promptly, assessed and learned from.",
                    "Suppliers that handle our information meet equivalent requirements.",
                    "Security objectives are set every year and measured.",
                ],
            ),
            (
                "Roles",
                "The board owns this policy. The ISMS owner maintains the ISMS and reports to the management review. Everyone who works for the organisation follows this policy and the topic-specific policies beneath it.",
            ),
            (
                "Compliance",
                "Breaches of this policy are handled under the disciplinary process. Exceptions need written approval from the ISMS owner and an expiry date.",
            ),
            (
                "Review",
                "The board reviews this policy every year and after significant change.",
            ),
        ],
    ),
    "access_procedure": dict(
        title="Access Control Procedure",
        type="procedure",
        folder="Procedures",
        tags=["access", "identity"],
        description="Defines account lifecycle, approval, review, and privileged access handling.",
        linked=110,
        controls={
            ISO: ["A.5.15", "A.5.16", "A.5.18", "A.8.2"],
            SOC2: ["CC6.1", "CC6.2", "CC6.3"],
            PCI: ["7.2", "8.2"],
        },
        sections=[
            (
                "Purpose",
                "This procedure makes sure people have the access their role needs, and no more, for as long as they need it.",
            ),
            (
                "Requesting access",
                [
                    "Requests are raised through the service desk and name the system and the role.",
                    "The line manager and the system owner approve each request.",
                    "Access is granted through role-based groups in the identity provider.",
                ],
            ),
            (
                "Privileged access",
                [
                    "Administrators use separate administrative accounts protected by MFA.",
                    "Production administration uses time-limited elevation with a ticket reference.",
                    "Shared administrative credentials are kept in the password vault and rotated after use.",
                ],
            ),
            (
                "Reviews and leavers",
                "System owners review user access every quarter and privileged access every month. Leavers are suspended in the identity provider on their last working day, and their access to source control and cloud consoles is removed the same day.",
            ),
        ],
    ),
    "incident_process": dict(
        title="Incident Response Process",
        type="process",
        folder="Procedures",
        tags=["incident", "response"],
        description="Describes preparation, triage, response, communication, and lessons learned.",
        linked=100,
        controls={
            ISO: ["A.5.24", "A.5.25", "A.5.26", "A.5.27", "A.5.28", "A.6.8"],
            SOC2: ["CC7.3", "CC7.4", "CC7.5"],
            PCI: ["12.10"],
        },
        sections=[
            (
                "Reporting",
                "Anyone who notices a possible security event reports it to the service desk or with the report-phishing button. Monitoring alerts open a ticket automatically.",
            ),
            (
                "Assessment",
                [
                    "The on-call engineer assesses each event within 30 minutes during working hours.",
                    "Events that affect customers, personal data or production availability become incidents.",
                    "Severity follows the matrix in the on-call handbook.",
                ],
            ),
            (
                "Response",
                "The incident lead contains the threat, preserves evidence with a chain-of-custody record, eradicates the cause and restores service from known-good sources.",
            ),
            (
                "Communication",
                "The incident lead updates stakeholders on an agreed rhythm. The data protection officer decides on regulator and customer notification within 72 hours of awareness.",
            ),
            (
                "Learning",
                "Every major incident has a review within ten working days. Actions are tracked to closure and reported to the management review.",
            ),
        ],
    ),
    "supplier_standard": dict(
        title="Supplier Management Standard",
        type="standard",
        folder="Standards",
        tags=["supplier", "third-party"],
        description="Defines supplier due diligence, assurance, contractual security, and review cadence.",
        linked=95,
        controls={
            ISO: ["A.5.19", "A.5.20", "A.5.21", "A.5.22", "A.5.23"],
            SOC2: ["CC9.2"],
            PCI: ["12.8"],
        },
        sections=[
            (
                "Scope",
                "This standard applies to every supplier that stores, processes or can access the organisation's information, including cloud services and contractors.",
            ),
            (
                "Before contract",
                [
                    "Rate each supplier as critical, important or standard.",
                    "Collect assurance proportionate to the rating, such as an ISO 27001 certificate or a SOC 2 report.",
                    "Include confidentiality, breach notification, audit and exit clauses in the contract.",
                ],
            ),
            (
                "During the relationship",
                "Critical suppliers are reviewed every year and important suppliers every two years. Reviews check assurance evidence, incidents, service levels and changes to sub-processors.",
            ),
            (
                "Exit",
                "Access is removed, data is returned or destroyed with a certificate, and the supplier record is closed.",
            ),
        ],
    ),
    "secure_dev_guideline": dict(
        title="Secure Development Guideline",
        type="guideline",
        folder="Standards",
        tags=["engineering", "sdlc"],
        description="Provides secure coding, review, dependency, and release guidance.",
        linked=90,
        controls={
            ISO: ["A.8.25", "A.8.27", "A.8.28", "A.8.29"],
            SOC2: ["CC8.1"],
            PCI: ["6.2"],
        },
        sections=[
            (
                "Design",
                "New features with security impact start with a short threat model recorded in the pull request or the design document.",
            ),
            (
                "Coding",
                [
                    "Use the framework's parameterised queries, output encoding and CSRF protection.",
                    "Keep secrets out of source control; the secret scanner blocks known patterns.",
                    "Pin dependencies and review new ones before adding them.",
                ],
            ),
            (
                "Review and testing",
                "Every change has a peer review. CI runs unit tests, static analysis, dependency checks and a licence check before merge; a dynamic scan runs against staging every night.",
            ),
            (
                "Release",
                "Releases are built by CI from the main branch, signed and deployed through the change management procedure.",
            ),
        ],
    ),
    "acceptable_use": dict(
        title="Acceptable Use Policy",
        type="policy",
        folder="Policies",
        tags=["policy", "staff"],
        description="Sets expectations for the use of devices, accounts, email and information.",
        linked=115,
        controls={
            ISO: ["A.5.10", "A.6.7", "A.7.7", "A.8.1"],
            PCI: ["12.2"],
        },
        sections=[
            (
                "Purpose",
                "This policy describes how staff and contractors use the organisation's devices, accounts and information safely.",
            ),
            (
                "Expectations",
                [
                    "Use company devices and approved services for company information.",
                    "Lock your screen when you step away and clear your desk at the end of the day.",
                    "Keep passwords private and use the password manager.",
                    "Report lost devices and suspicious messages straight away.",
                    "When working remotely, use trusted networks and keep screens out of public view.",
                ],
            ),
            (
                "Monitoring",
                "Security logging covers company systems for the purposes described in the privacy notice for staff.",
            ),
            (
                "Acknowledgement",
                "Everyone acknowledges this policy when they join and every year afterwards.",
            ),
        ],
    ),
    "asset_management": dict(
        title="Asset Management Procedure",
        type="procedure",
        folder="Procedures",
        tags=["assets", "inventory"],
        description="Covers the asset inventory, ownership, return of assets and secure disposal.",
        linked=85,
        controls={ISO: ["A.5.9", "A.5.11", "A.7.9", "A.7.14"]},
        sections=[
            (
                "Inventory",
                "The asset register lists hardware, cloud services, information assets and software with an owner for each. Device management and cloud inventories reconcile against it every month.",
            ),
            (
                "Use off-site",
                "Laptops are encrypted and enrolled in device management before issue. Staff keep devices with them when travelling and report loss within one hour.",
            ),
            (
                "Return",
                "Leavers return all equipment and access badges on their last day; the service desk records the return on the leaver ticket.",
            ),
            (
                "Disposal",
                "Equipment is wiped or destroyed by an approved supplier, who provides a certificate listing each serial number.",
            ),
        ],
    ),
    "classification": dict(
        title="Information Classification and Handling Standard",
        type="standard",
        folder="Standards",
        tags=["classification", "handling"],
        description="Defines classification levels, labelling and the handling rules for each level.",
        linked=80,
        controls={
            ISO: ["A.5.12", "A.5.13", "A.5.14", "A.5.33"],
            SOC2: ["C1.1"],
        },
        sections=[
            (
                "Levels",
                [
                    "Public: approved for anyone.",
                    "Internal: for staff and contractors.",
                    "Confidential: customer data, personal data and commercially sensitive material.",
                    "Restricted: credentials, keys and incident evidence.",
                ],
            ),
            (
                "Labelling",
                "Documents carry their level in the header or a drive label. Systems inherit the highest level of the information they hold.",
            ),
            (
                "Transfer",
                "Confidential and restricted information leaves the organisation only through approved encrypted channels and with an agreement covering its protection.",
            ),
        ],
    ),
    "cryptography": dict(
        title="Cryptography and Key Management Standard",
        type="standard",
        folder="Standards",
        tags=["cryptography", "keys"],
        description="Sets approved algorithms, TLS settings and the key management lifecycle.",
        linked=75,
        controls={
            ISO: ["A.8.24"],
            SOC2: ["CC6.1-POF8", "CC6.1-POF9"],
            PCI: ["3.6", "3.7", "4.2"],
        },
        sections=[
            (
                "Approved cryptography",
                [
                    "TLS 1.2 or later with forward-secret cipher suites for data in transit.",
                    "AES-256 for data at rest through the cloud provider's key management service.",
                    "Password storage uses a memory-hard hashing function.",
                ],
            ),
            (
                "Key management",
                "Keys are generated in the key management service, rotated every year, and access to them is limited to the services that need them. Key administrators cannot read the data the keys protect.",
            ),
            (
                "Certificates",
                "Public certificates renew automatically and expiry is monitored with a 21-day warning.",
            ),
        ],
    ),
    "backup_restore": dict(
        title="Backup and Restore Procedure",
        type="procedure",
        folder="Procedures",
        tags=["backup", "resilience"],
        description="Describes backup scope, schedules, immutability and restore testing.",
        linked=70,
        controls={ISO: ["A.8.13", "A.8.14"], SOC2: ["A1.2"]},
        sections=[
            (
                "Scope and schedule",
                "Production databases and object storage are backed up every night, with point-in-time recovery for the primary database. Copies are kept for 35 days.",
            ),
            (
                "Protection",
                "Backups go to a vault in a separate hosting account with object locking, so no production credential can delete or alter them.",
            ),
            (
                "Monitoring",
                "Failed backup jobs raise an alert to the on-call engineer, who reruns the job and records the outcome.",
            ),
            (
                "Restore testing",
                "A restore is tested every month against the 60-minute recovery objective, including integrity and application checks.",
            ),
        ],
    ),
    "continuity": dict(
        title="Business Continuity and ICT Readiness Plan",
        type="process",
        folder="Plans and records",
        tags=["continuity", "resilience"],
        description="Sets recovery objectives, roles and the arrangements that keep security in place during disruption.",
        linked=65,
        controls={
            ISO: ["A.5.29", "A.5.30", "A.8.14"],
            SOC2: ["A1.3", "CC9.1"],
            PCI: ["12.10"],
        },
        sections=[
            (
                "Objectives",
                "The customer portal has a recovery time objective of four hours and a recovery point objective of 15 minutes.",
            ),
            (
                "Arrangements",
                [
                    "Production runs across two availability zones with automatic database failover.",
                    "Staff can work fully remotely if the office is unavailable.",
                    "Security controls such as MFA, logging and change approval stay in force during recovery.",
                ],
            ),
            (
                "Exercises",
                "The plan is exercised twice a year, alternating a tabletop and a technical failover, and updated after each exercise.",
            ),
        ],
    ),
    "logging": dict(
        title="Logging and Monitoring Standard",
        type="standard",
        folder="Standards",
        tags=["logging", "monitoring"],
        description="Defines what is logged, how logs are protected and how alerts are handled.",
        linked=60,
        controls={
            ISO: ["A.8.15", "A.8.16", "A.8.17"],
            SOC2: ["CC7.2"],
            PCI: ["10.2", "10.4", "10.6"],
        },
        sections=[
            (
                "What is logged",
                [
                    "Authentication, privilege use and administrative changes on every system.",
                    "Security tool alerts, firewall decisions and application errors.",
                    "Access to customer data exports.",
                ],
            ),
            (
                "Protection and retention",
                "Logs are shipped to central storage within one minute, protected from alteration and kept for 13 months. All systems synchronise their clocks with the approved time sources.",
            ),
            (
                "Monitoring",
                "Alert rules route to the on-call engineer. Security events are reviewed every working day and the review is recorded.",
            ),
        ],
    ),
    "vulnerability": dict(
        title="Vulnerability and Patch Management Procedure",
        type="procedure",
        folder="Procedures",
        tags=["vulnerability", "patching"],
        description="Sets scanning frequency, remediation targets and the exception process.",
        linked=55,
        controls={
            ISO: ["A.8.8", "A.8.19"],
            SOC2: ["CC7.1"],
            PCI: ["6.3", "6.3.3", "11.3"],
        },
        sections=[
            (
                "Identification",
                "Infrastructure, containers and dependencies are scanned every week, and threat intelligence advisories are triaged every working day.",
            ),
            (
                "Remediation targets",
                [
                    "Critical: within 14 days, or sooner when actively exploited.",
                    "High: within 30 days.",
                    "Medium and low: in the next scheduled maintenance.",
                ],
            ),
            (
                "Software installation",
                "Only packages from approved repositories are installed on servers, through configuration management. Unexpected installations raise an alert.",
            ),
            (
                "Exceptions",
                "A missed target needs a risk acceptance from the asset owner with compensating controls and a new date.",
            ),
        ],
    ),
    "change_management": dict(
        title="Change Management Procedure",
        type="procedure",
        folder="Procedures",
        tags=["change", "release"],
        description="Covers change requests, approval, separation of environments and emergency changes.",
        linked=50,
        controls={
            ISO: ["A.8.9", "A.8.31", "A.8.32"],
            SOC2: ["CC8.1"],
            PCI: ["6.5"],
        },
        sections=[
            (
                "Standard changes",
                "Pre-approved, low-risk changes follow a documented runbook and are recorded in the ticketing system.",
            ),
            (
                "Normal changes",
                "Each normal change has a ticket with a risk assessment, test evidence from staging and a rollback plan. A second engineer approves the deployment.",
            ),
            (
                "Environments",
                "Development, staging and production use separate accounts and credentials. Production data is never copied to lower environments without masking.",
            ),
            (
                "Emergency changes",
                "Emergency changes are approved by the on-call lead and reviewed retrospectively within two working days.",
            ),
        ],
    ),
    "network_security": dict(
        title="Network Security Standard",
        type="standard",
        folder="Standards",
        tags=["network", "firewall"],
        description="Sets segmentation, firewall and remote administration requirements.",
        linked=45,
        controls={
            ISO: ["A.8.20", "A.8.21", "A.8.22"],
            SOC2: ["CC6.6"],
            PCI: ["1.2", "1.3", "1.4"],
        },
        sections=[
            (
                "Segmentation",
                "Production, staging and corporate networks are separated. Databases accept connections only from the application tier.",
            ),
            (
                "Rules",
                [
                    "Inbound access is denied by default and every allowed rule has an owner and a business reason.",
                    "Administrative access goes through the bastion host with MFA.",
                    "Firewall rules are reviewed every six months.",
                ],
            ),
            (
                "Network services",
                "Internet-facing services sit behind the managed web application firewall and DDoS protection.",
            ),
        ],
    ),
    "endpoint": dict(
        title="Endpoint and Malware Protection Standard",
        type="standard",
        folder="Standards",
        tags=["endpoint", "malware"],
        description="Defines the baseline for laptops, mobile devices and malware protection.",
        linked=42,
        controls={
            ISO: ["A.7.10", "A.8.1", "A.8.7"],
            SOC2: ["CC6.8"],
            PCI: ["5.2", "5.3"],
        },
        sections=[
            (
                "Device baseline",
                [
                    "Full-disk encryption, a screen lock after five minutes and automatic updates.",
                    "Enrolment in device management, with remote lock and wipe.",
                    "Removable storage is blocked unless the security lead approves an exception.",
                ],
            ),
            (
                "Malware protection",
                "Endpoint protection runs on every laptop and server, updates its signatures daily and sends detections to central monitoring.",
            ),
            (
                "Mobile devices",
                "Personal phones may access email and chat only through the managed work profile.",
            ),
        ],
    ),
    "physical": dict(
        title="Physical and Environmental Security Policy",
        type="policy",
        folder="Policies",
        tags=["physical", "premises"],
        description="Covers the office perimeter, entry control, secure areas, utilities and equipment care.",
        linked=40,
        controls={
            ISO: [
                "A.7.1",
                "A.7.2",
                "A.7.3",
                "A.7.4",
                "A.7.5",
                "A.7.6",
                "A.7.8",
                "A.7.11",
                "A.7.13",
            ],
            SOC2: ["CC6.4"],
            PCI: ["9.1", "9.2"],
        },
        sections=[
            (
                "Perimeter and entry",
                "The office entrance uses badge access, and visitors sign in, wear a visible badge and are escorted. Badge access is reviewed every quarter.",
            ),
            (
                "Secure areas",
                "The communications room is locked, monitored by CCTV and open only to named staff and escorted suppliers.",
            ),
            (
                "Environment and utilities",
                [
                    "Fire detection and suppression are inspected every six months.",
                    "A UPS protects network equipment and is tested every quarter.",
                    "Equipment is sited away from water and heat sources.",
                ],
            ),
            (
                "Maintenance",
                "Equipment is maintained according to the manufacturer's schedule, and supplier visits are recorded.",
            ),
        ],
    ),
    "people_security": dict(
        title="People Security Procedure: Joiners, Movers and Leavers",
        type="procedure",
        folder="Procedures",
        tags=["hr", "people"],
        description="Covers screening, terms of employment, confidentiality, role changes, discipline and leaving.",
        linked=38,
        controls={
            ISO: ["A.6.1", "A.6.2", "A.6.4", "A.6.5", "A.6.6"],
            SOC2: ["CC6.2"],
            PCI: ["12.7"],
        },
        sections=[
            (
                "Joiners",
                [
                    "Screening proportionate to the role: identity, right to work, references and, for privileged roles, a criminal record check.",
                    "Contracts include information security responsibilities and confidentiality terms.",
                    "Contractors and suppliers sign a non-disclosure agreement before access is granted.",
                ],
            ),
            (
                "Movers",
                "A role change triggers an access review; access the new role does not need is removed within five working days.",
            ),
            (
                "Leavers",
                "HR raises the leaver ticket at least five days ahead. Accounts are suspended on the last day, equipment is returned, and continuing confidentiality duties are confirmed in the exit letter.",
            ),
            (
                "Discipline",
                "Security breaches are handled under the disciplinary process, which is fair and proportionate and is reviewed with HR every year.",
            ),
        ],
    ),
    "awareness": dict(
        title="Security Awareness and Training Programme",
        type="process",
        folder="Plans and records",
        tags=["training", "awareness"],
        description="Plans awareness training, phishing simulations and role-based training for the year.",
        linked=35,
        controls={
            ISO: ["A.6.3", "A.6.8"],
            SOC2: ["CC1.4", "CC2.2"],
            PCI: ["12.6"],
        },
        clauses=["7.2", "7.3"],
        sections=[
            (
                "Programme",
                [
                    "Induction training in the first week, then an annual refresher for everyone.",
                    "Quarterly phishing simulations with immediate feedback.",
                    "Secure development training for engineers and incident training for on-call staff.",
                ],
            ),
            (
                "Measuring",
                "Completion and simulation results are reported to the management review; anyone who misses training receives a reminder and their manager is informed after two weeks.",
            ),
        ],
    ),
    "privacy": dict(
        title="Privacy and Personal Data Protection Policy",
        type="policy",
        folder="Policies",
        tags=["privacy", "personal data"],
        description="Sets out how personal data is collected, used, retained and deleted.",
        linked=32,
        controls={
            ISO: ["A.5.34", "A.8.10", "A.8.11"],
            SOC2: ["P1.1", "P1.6", "P1.8"],
        },
        sections=[
            (
                "Principles",
                "Personal data is processed lawfully, for stated purposes, kept accurate and kept only as long as needed.",
            ),
            (
                "Rights",
                "Requests from individuals are logged and answered within one month. The data protection officer reviews every response before it is sent.",
            ),
            (
                "Minimisation",
                "Production personal data is masked before use in testing, and deleted at the end of its retention period by an automated job.",
            ),
        ],
    ),
    "legal_register": dict(
        title="Legal, Regulatory and Contractual Requirements Register",
        type="record",
        folder="Plans and records",
        tags=["legal", "compliance"],
        description="Lists the laws, regulations and contract terms that shape the ISMS, with owners.",
        linked=30,
        controls={ISO: ["A.5.5", "A.5.31", "A.5.32", "A.5.33"]},
        clauses=["4.2"],
        sections=[
            (
                "Entries",
                [
                    "Data protection law: data protection officer; reviewed every year.",
                    "Card scheme rules and PCI DSS for card handling: security lead.",
                    "Customer contract security schedules: ISMS owner.",
                    "Software licence terms: service desk lead.",
                ],
            ),
            (
                "Authorities",
                "The register lists the regulator, law enforcement and national cyber security contacts, and who may contact them.",
            ),
            (
                "Review",
                "The register is reviewed every six months and whenever a new market or service is planned.",
            ),
        ],
    ),
    "threat_intelligence": dict(
        title="Threat Intelligence Procedure",
        type="procedure",
        folder="Procedures",
        tags=["threat intelligence"],
        description="Describes intelligence sources, triage and how advisories turn into actions.",
        linked=28,
        controls={ISO: ["A.5.7"], SOC2: ["CC3.4"]},
        sections=[
            (
                "Sources",
                "National advisories, supplier security bulletins and the vulnerability feeds of the technologies in use.",
            ),
            (
                "Triage",
                "The security lead reviews new intelligence every working day and records whether it applies, its urgency and the owner of any action.",
            ),
            (
                "Sharing",
                "Relevant intelligence is shared with engineering in the weekly briefing.",
            ),
        ],
    ),
    "secure_configuration": dict(
        title="Secure Configuration Baseline",
        type="standard",
        folder="Standards",
        tags=["hardening", "configuration"],
        description="Hardening baselines for servers, cloud accounts and privileged tools.",
        linked=25,
        controls={
            ISO: ["A.8.5", "A.8.9", "A.8.18"],
            SOC2: ["CC7.1-POF1"],
            PCI: ["2.2", "8.3"],
        },
        sections=[
            (
                "Baseline",
                [
                    "Servers are built from configuration management with unnecessary services removed.",
                    "SSH accepts keys only, through the bastion host.",
                    "Cloud accounts enforce MFA and block public storage by default.",
                ],
            ),
            (
                "Privileged utilities",
                "Utilities that can bypass controls, such as packet capture or database consoles, run only through sudo with logging and are removed when no longer needed.",
            ),
            (
                "Drift",
                "Configuration drift is detected daily and corrected through the change management procedure.",
            ),
        ],
    ),
    "cloud_security": dict(
        title="Cloud Services Security Standard",
        type="standard",
        folder="Standards",
        tags=["cloud", "saas"],
        description="Requirements for adopting, configuring and exiting cloud services.",
        linked=22,
        controls={ISO: ["A.5.23", "A.8.3", "A.8.26"], SOC2: ["CC6.1"]},
        sections=[
            (
                "Adoption",
                "A new cloud service needs a supplier assessment, a named owner and a record of the data it will hold.",
            ),
            (
                "Configuration",
                "Single sign-on, MFA and audit logging are switched on before any data is loaded. Sharing outside the organisation is restricted to approved domains.",
            ),
            (
                "Exit",
                "Each service has an exit plan covering data export, deletion and the removal of integrations.",
            ),
        ],
    ),
    "retention_schedule": dict(
        title="Records Retention and Deletion Schedule",
        type="record",
        folder="Plans and records",
        tags=["records", "retention"],
        description="Sets retention periods and deletion methods for each record type.",
        linked=20,
        controls={ISO: ["A.5.33", "A.8.10"], SOC2: ["C1.2", "P1.5"]},
        sections=[
            (
                "Schedule",
                [
                    "Customer account data: life of the contract plus 12 months.",
                    "Security logs: 13 months.",
                    "Financial records: six years, on write-once storage.",
                    "Recruitment records for unsuccessful candidates: six months.",
                ],
            ),
            (
                "Deletion",
                "Automated jobs delete expired records and log the number deleted. Media is destroyed with a certificate of destruction.",
            ),
        ],
    ),
    "risk_methodology": dict(
        title="Risk Assessment and Treatment Methodology",
        type="process",
        folder="Plans and records",
        tags=["risk"],
        description="How risks are identified, rated on the 5x5 register, treated and accepted.",
        linked=125,
        controls={SOC2: ["CC3.2"]},
        clauses=["6.1", "6.1.3", "8.2", "8.3"],
        sections=[
            (
                "Identification",
                "Risks are identified per asset and from PESTLE(E), SWOT, incidents and audit findings. Each risk has an owner.",
            ),
            (
                "Rating",
                "Likelihood and impact are rated from one to five; their product gives the inherent score. The residual rating assumes the selected controls operate effectively.",
            ),
            (
                "Treatment",
                "Risks scoring 15 or more need a treatment plan within 30 days. Residual risks above 8 need acceptance by the board.",
            ),
            (
                "Review",
                "The register is reviewed every quarter and after significant change, and the Statement of Applicability is updated with it.",
            ),
        ],
    ),
    "isms_scope": dict(
        title="ISMS Scope Statement",
        type="record",
        folder="Plans and records",
        tags=["scope", "context"],
        description="Defines the boundaries of the ISMS, its interfaces and its exclusions.",
        linked=130,
        controls={},
        clauses=["4.1", "4.2", "4.3"],
        sections=[
            (
                "Scope",
                "The design, development, hosting and support of the customer portal, the people who deliver it and the head office.",
            ),
            (
                "Interfaces",
                "Managed hosting, the identity provider and the payment service provider are suppliers within the scope's boundary; their own controls are assessed through supplier management.",
            ),
            (
                "Context",
                "The scope reflects the PESTLE(E) analysis and the needs of customers, staff, regulators and suppliers recorded as interested parties.",
            ),
        ],
    ),
    "internal_audit": dict(
        title="Internal Audit Programme",
        type="process",
        folder="Plans and records",
        tags=["audit"],
        description="Plans internal audits across the ISMS over a three-year cycle.",
        linked=18,
        controls={ISO: ["A.5.35", "A.5.36"], SOC2: ["CC4.1"]},
        clauses=["9.2"],
        sections=[
            (
                "Programme",
                "Every clause and applicable Annex A control is audited at least once in three years; higher-risk areas every year.",
            ),
            (
                "Independence",
                "Auditors never audit their own work. An external auditor reviews the ISMS every year.",
            ),
            (
                "Reporting",
                "Findings are recorded against the audit with evidence and owners, and summarised for the management review.",
            ),
        ],
    ),
    "management_review": dict(
        title="Management Review Procedure",
        type="procedure",
        folder="Procedures",
        tags=["governance"],
        description="Sets the inputs, attendees and outputs of the ISMS management review.",
        linked=15,
        controls={ISO: ["A.5.4"]},
        clauses=["9.3", "10.1", "10.2"],
        sections=[
            (
                "Rhythm",
                "Management reviews take place every quarter and are chaired by the ISMS owner with a board member present.",
            ),
            (
                "Inputs",
                [
                    "Status of actions from previous reviews and of nonconformities.",
                    "Effectiveness measures, objectives and audit results.",
                    "Changes in context, risks and interested parties' needs.",
                ],
            ),
            (
                "Outputs",
                "Decisions on improvements, resources and changes to the ISMS, recorded in the meeting minutes with owners and dates.",
            ),
        ],
    ),
    "pentest_summary": dict(
        title="External Penetration Test Report Summary",
        type="record",
        folder="Plans and records",
        tags=["testing", "penetration test"],
        description="Summary of the annual external penetration test and the remediation status.",
        linked=12,
        controls={
            ISO: ["A.8.8", "A.8.29", "A.8.34"],
            SOC2: ["CC7.1"],
            PCI: ["11.4"],
        },
        sections=[
            (
                "Engagement",
                "A fictional accredited tester assessed the customer portal and its API over five days, within rules of engagement that limited production testing to an agreed window.",
            ),
            (
                "Findings",
                [
                    "One high: verbose error messages on an API endpoint (fixed and retested).",
                    "Two medium: missing rate limit on password reset; outdated TLS cipher on a legacy host.",
                    "Four low and informational findings.",
                ],
            ),
            (
                "Remediation",
                "Findings are tracked through the vulnerability procedure; the remaining medium finding is due within 30 days.",
            ),
        ],
    ),
    "project_checklist": dict(
        title="Project Security Checklist",
        type="guideline",
        folder="Standards",
        tags=["projects"],
        description="Security questions every project answers at initiation, design and go-live.",
        linked=10,
        controls={ISO: ["A.5.8", "A.8.26", "A.8.27"]},
        sections=[
            (
                "Initiation",
                [
                    "What information will the project create, use or change, and how is it classified?",
                    "Which suppliers and cloud services are involved?",
                ],
            ),
            (
                "Design",
                "Record security requirements and a threat model; the security lead signs off the design.",
            ),
            (
                "Go-live",
                "Testing evidence, an updated asset register entry and an operations handover are in place before launch.",
            ),
        ],
    ),
    "test_data": dict(
        title="Test Data and Data Masking Guideline",
        type="guideline",
        folder="Standards",
        tags=["testing", "masking"],
        description="How test data is created, masked and protected outside production.",
        linked=8,
        controls={ISO: ["A.8.11", "A.8.31", "A.8.33"]},
        sections=[
            (
                "Test data",
                "Prefer synthetic data. Where production data is needed, the masking job replaces names, contact details and identifiers before the copy leaves production.",
            ),
            (
                "Protection",
                "Staging data is protected to the same level as production and refreshed at most monthly; refreshes are logged.",
            ),
        ],
    ),
}


def render(sections) -> str:
    """Document HTML from plain text, escaped and then sanitised as on save."""
    parts = [f"<p><em>{escape(NOTICE)}</em></p>"]
    for heading, body in sections:
        parts.append(f"<h2>{escape(heading)}</h2>")
        if isinstance(body, list):
            items = "".join(f"<li>{escape(text)}</li>" for text in body)
            parts.append(f"<ul>{items}</ul>")
        else:
            parts.append(f"<p>{escape(body)}</p>")
    return sanitize_rich_text_html("".join(parts))


def approved_at(seeder, days_ago: int) -> datetime:
    return datetime.combine(seeder.seed_date - timedelta(days=days_ago), time(9, 30))


def seed_documents(seeder):
    db = seeder.db
    folders = {
        name: seeder.upsert(
            IsmsDocumentFolder, {"name": seeder.label(name), "parent_id": None}
        )
        for name in sorted({spec["folder"] for spec in DOCUMENTS.values()})
    }
    docs = {}
    for key, spec in DOCUMENTS.items():
        title = seeder.label(spec["title"])
        html = render(spec["sections"])
        created = approved_at(seeder, spec["linked"])
        values = {
            "document_type": spec["type"],
            "description": spec["description"],
            "external_url": None,
            "folder_id": folders[spec["folder"]].id,
            "tags": list(spec["tags"]),
            "created_by_user_id": seeder.user.id,
            "created_at": created,
            "updated_at": created,
        }
        existing = db.query(IsmsDocument).filter_by(title=title).first()
        # Content arrives once; later edits made in KEEN are kept.
        if existing is None or not existing.content_version:
            values.update(content_html=html, content_version=1)
        doc = seeder.upsert(IsmsDocument, {"title": title}, values)
        if doc.content_version == 1 and doc.content_html == html:
            seeder.ensure_link(
                IsmsDocumentRevision,
                {"document_id": doc.id, "version": 1},
                {
                    "content_html": html,
                    "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                    "created_by_user_id": seeder.user.id,
                    "created_at": created,
                },
            )
        for framework, refs in spec["controls"].items():
            seeder.link_isms_controls("document", doc.id, refs, framework)
        seeder.link_isms_clauses("document", doc.id, spec.get("clauses", ()))
        docs[key] = doc
    return docs


def _link_entry(db, external_id):
    return (
        db.query(Event)
        .filter_by(source=DIARY_SOURCE, external_id=external_id)
        .one_or_none()
    )


def seed_link_diary(seeder):
    """Record the automatic diary entry for each seeded document's links.

    The application's own link synchronisation writes each entry, its JSON
    artefact and its automatic mappings. A new entry takes the time its
    document was linked; a later run leaves existing entries to the
    application. A dry run skips this step because artefacts are stored
    outside the database transaction.
    """
    if seeder.config.dry_run:
        seeder.stats["document link diary entries.skipped in dry run"] += 1
        return
    db = seeder.db
    docs = (
        db.query(IsmsDocument)
        .filter(IsmsDocument.title.like(seeder.prefix + " %"))
        .order_by(IsmsDocument.created_at, IsmsDocument.title)
        .all()
    )
    for doc in docs:
        external_id = link_entry_external_id("document", doc.id)
        existed = _link_entry(db, external_id) is not None
        sync_entity_link_diary(db, "document", doc.id, seeder.user)
        event = _link_entry(db, external_id)
        if event is None:
            continue
        if existed:
            seeder.stats["document link diary entries.unchanged"] += 1
            continue
        linked = doc.created_at + timedelta(minutes=12)
        if linked < event.timestamp:
            event.timestamp = linked
            db.flush()
        seeder.stats["document link diary entries.created"] += 1
