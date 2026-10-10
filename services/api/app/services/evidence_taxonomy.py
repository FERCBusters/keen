"""Curated, one-hop vocabulary for evidence suggestions.

Aliases identify a concept in the mapping name/sample; targets identify that
concept in the current control catalogue. Phrases remain intact: a firewall
must not expand into every control containing the word 'security'. Add aliases
and target phrases here, with a regression example in test_control_suggestions.
These are relevance hints, not assertions that evidence satisfies a control.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Concept:
    name: str
    aliases: tuple[str, ...]
    targets: tuple[str, ...]


def concept(name, aliases, targets):
    return Concept(name, tuple(aliases.split("|")), tuple(targets.split("|")))


CONCEPTS = (
    concept(
        "Network protection",
        "firewall|firewalls|iptables|nftables|ufw|packet filtering|network acl|network security|network traffic|network services|network protection",
        "network security|security of networks|security of network services|network services security|network protection|firewall|packet filtering",
    ),
    concept(
        "Network segmentation",
        "vlan|network segmentation|network segregation|microsegmentation|micro segmentation|zero trust network",
        "segregation of networks|network segmentation|network segregation|network isolation",
    ),
    concept(
        "Web protection",
        "waf|web application firewall|web filtering|url filtering|web proxy|blocked website",
        "web filtering|web application firewall|application security|network security",
    ),
    concept(
        "Malware defence",
        "antivirus|anti virus|antimalware|anti malware|malware|ransomware|edr|endpoint detection|clamav|defender",
        "malware|malicious software|endpoint protection|endpoint security",
    ),
    concept(
        "Vulnerability and patch management",
        "patch|patching|patched|package upgrade|package update|apt|dpkg|rpm|dnf|yum|cve|vulnerability|vulnerability scan|security update",
        "technical vulnerabilities|vulnerability management|vulnerabilities|patch management|security updates|software updates",
    ),
    concept(
        "Secure configuration",
        "hardening|configuration drift|baseline|cis benchmark|secure configuration|configuration management|ansible",
        "configuration management|secure configuration|hardening|configuration baseline",
    ),
    concept(
        "Change management",
        "deployment|release|change approval|change request|pull request|merge request|peer review|ci cd|jenkins|software change",
        "change management|change control|changes to information systems|software change|review of changes",
    ),
    concept(
        "Secure development",
        "sast|dast|code scanning|code review|secure coding|sql injection|xss|dependency scan|software development",
        "secure coding|secure development|security testing|application security requirements|secure system engineering",
    ),
    concept(
        "Authentication",
        "login|logon|ssh|sshd|mfa|2fa|totp|webauthn|yubikey|password|authentication|single sign on|sso",
        "secure authentication|authentication information|authentication|password management",
    ),
    concept(
        "Access rights",
        "user provisioning|offboarding|leaver|joiner|account removal|account disable|access review|rbac|iam|user access|permission review",
        "access rights|identity management|access control|user access|account management",
    ),
    concept(
        "Privileged access",
        "sudo|su command|root access|privileged access|privilege escalation|administrator access|least privilege|pam access",
        "privileged access rights|privileged utility programs|privileged access|least privilege",
    ),
    concept(
        "Logging",
        "syslog|journald|auditd|audit trail|log collection|log retention|centralised logging|centralized logging|loki|logging",
        "logging|audit logs|log management|audit trails",
    ),
    concept(
        "Security monitoring",
        "siem|ossec|wazuh|ids|ips|intrusion detection|intrusion prevention|security alert|security monitoring|suspicious activity",
        "monitoring activities|security monitoring|intrusion detection|information security events",
    ),
    concept(
        "Incident response",
        "incident|breach|incident response|active response|security event triage|postmortem|post mortem|containment",
        "incident management|incident response|response to information security incidents|learning from information security incidents|assessment and decision on information security events",
    ),
    concept(
        "Evidence preservation",
        "forensic|forensics|chain of custody|evidence preservation|immutable evidence|object lock",
        "collection of evidence|evidence preservation|protection of records|records protection",
    ),
    concept(
        "Backup and recovery",
        "backup|backups|restore|restoration|recovery test|snapshot|restic|borg|veeam",
        "information backup|backup|restoration|recovery procedures",
    ),
    concept(
        "Continuity and resilience",
        "disaster recovery|business continuity|bcp|dr test|failover|redundancy|resilience|resiliency|outage exercise",
        "business continuity|ict readiness|redundancy|continuity planning|disaster recovery",
    ),
    concept(
        "Availability and capacity",
        "uptime|sla|slo|availability|capacity|latency|service level|prometheus|effectiveness measure",
        "capacity management|availability|service level|monitoring measurement|performance evaluation",
    ),
    concept(
        "Supplier assurance",
        "supplier|vendor|third party|outsourcing|supply chain|supplier review|vendor assessment",
        "supplier relationships|supplier agreements|supplier services|supply chain|third party|external providers",
    ),
    concept(
        "Cloud services",
        "cloud|aws|azure|gcp|saas|cloud provider",
        "cloud services|cloud security|cloud computing",
    ),
    concept(
        "Cryptography",
        "encryption|encrypted|tls|ssl|certificate|cryptography|kms|key rotation|key management|letsencrypt",
        "cryptography|cryptographic|encryption|key management",
    ),
    concept(
        "Data transfer",
        "file transfer|sftp|scp|data transfer|information transfer|secure email",
        "information transfer|data transfer|electronic messaging",
    ),
    concept(
        "Data leakage prevention",
        "dlp|data leakage|data leak|exfiltration|data loss prevention|secret scanning",
        "data leakage prevention|data loss prevention|information leakage",
    ),
    concept(
        "Data masking",
        "redaction|redact|masking|pseudonymisation|pseudonymization|anonymisation|anonymization",
        "data masking|pseudonymisation|pseudonymization|anonymisation|anonymization",
    ),
    concept(
        "Data retention and disposal",
        "retention|purge|deletion|disposal|shredding|secure erase|sanitisation|sanitization",
        "information deletion|retention|secure disposal|disposal of media|disposal or re use|sanitisation|sanitization",
    ),
    concept(
        "Privacy",
        "gdpr|pii|personal data|privacy|data subject|consent",
        "privacy|personally identifiable information|personal data|data protection",
    ),
    concept(
        "Asset management",
        "asset inventory|asset register|cmdb|device inventory|hardware inventory|software inventory|asset ownership",
        "inventory of information|asset inventory|asset management|ownership of assets",
    ),
    concept(
        "Information classification",
        "classification|labelling|labeling|sensitivity label|data classification",
        "classification of information|labelling of information|information classification|labeling of information",
    ),
    concept(
        "Physical access",
        "door access|badge access|visitor|keycard|physical access|door lock|cctv",
        "physical entry|physical security monitoring|physical security perimeters|securing offices",
    ),
    concept(
        "Environmental protection",
        "fire alarm|smoke detector|flood|temperature alarm|ups|power supply|cooling",
        "physical and environmental threats|supporting utilities|equipment siting|environmental protection",
    ),
    concept(
        "Security awareness",
        "training|awareness|phishing simulation|phishing exercise|staff induction|security education",
        "awareness education and training|awareness|competence|training",
    ),
    concept(
        "Personnel assurance",
        "background check|screening|employment contract|confidentiality agreement|nda",
        "screening|terms and conditions of employment|confidentiality or non disclosure agreements",
    ),
    concept(
        "Remote working",
        "remote work|home working|teleworking|work from home|byod|mobile device",
        "remote working|teleworking|user endpoint devices|mobile devices",
    ),
    concept(
        "Policies and documents",
        "policy review|policy approval|document control|bookstack|policy acknowledgement|procedure review",
        "policies for information security|documented information|document control|documented operating procedures",
    ),
    concept(
        "Audit and assurance",
        "internal audit|external audit|audit finding|assurance review|audit programme",
        "internal audit|independent review|audit programme|audit program|compliance with policies",
    ),
    concept(
        "Risk assessment",
        "risk assessment|risk register|risk treatment|risk acceptance|risk review",
        "risk assessment|risk treatment|actions to address risks",
    ),
    concept(
        "Management review and improvement",
        "management review|corrective action|nonconformity|non conformity|continuous improvement|continual improvement",
        "management review|corrective action|nonconformity|continual improvement",
    ),
    concept(
        "Scheduled operations",
        "cron|crontab|scheduled job|batch job|job failure|systemd timer",
        "documented operating procedures|monitoring activities|operational planning",
    ),
    concept(
        "Time synchronisation",
        "ntp|chrony|time sync|clock drift|clock synchronisation|clock synchronization",
        "clock synchronisation|clock synchronization|time synchronisation|time synchronization",
    ),
    concept(
        "AI assurance",
        "prompt injection|llm|artificial intelligence|ai model|model drift|data poisoning|ai agent",
        "artificial intelligence|prompt injection|ai model|model monitoring|training data|data poisoning",
    ),
)
