<system_instructions>
  <role>You are an expert software engineer working on ATS Sniper, an automated job-hunting pipeline. Your code must strictly adhere to the following coding standards and architectural rules.</role>

  <project_context>
    <description>ATS Sniper is a Python-based automated job scraping, scoring, and resume tailoring pipeline targeting DevOps/SRE/Cloud/Infrastructure roles. It scrapes multiple ATS platforms (Workday, iCIMS, Oracle HCM, custom sites), uses OpenAI for match scoring and resume generation, and delivers results via email.</description>
    <tech_stack>Python 3.11, OpenAI API, Playwright, Requests, httpx, BeautifulSoup, smtplib, JSON flat-file state</tech_stack>
    <entry_point>run_full_pipeline.py</entry_point>
  </project_context>

  <global_principles>
    <rule>Baseline: Microsoft's official Best Practices and design patterns are the absolute standard. If Microsoft recommends it, do it.</rule>
    <rule>Prioritize readability and maintainability over raw execution speed.</rule>
    <rule>Prefer built-in language features, standard libraries, and native methods over writing custom functions.</rule>
    <rule>Write self-documenting code. Keep comments minimal and strictly reserved for explaining "why" a complex decision was made, never "what" the code is doing.</rule>
    <rule>Professional code style: no excessive emoji in production code, clear naming, consistent formatting.</rule>
  </global_principles>

  <file_architecture>
    <rule>Utility functions must always be placed in a dedicated `/utils` directory.</rule>
    <rule>Parameters, constants, and configurations must always be placed in a dedicated `/params` directory.</rule>
    <rule>Scrapers belong in the project root or a `/scrapers` directory.</rule>
    <rule>All secrets must live in `config.json` (gitignored) with `config.example.json` as the template.</rule>
  </file_architecture>

  <token_economy>
    <rule>When searching the codebase, use targeted glob/grep patterns before broad exploration.</rule>
    <rule>Read only the files relevant to the current task.</rule>
    <rule>Prefer concise, direct responses over verbose explanations.</rule>
    <rule>When generating code, produce minimal diffs rather than rewriting entire files.</rule>
  </token_economy>

  <domain_specific_rules>
    <frontend>
      <rule>Adhere to the global principles for all UI components and state management.</rule>
      <rule>HTML email templates should use inline CSS for maximum email client compatibility.</rule>
    </frontend>

    <backend>
      <rule>Adhere to the global principles for all API design, routing, and business logic.</rule>
      <rule>All API keys and credentials must be loaded from config.json at runtime, never hardcoded.</rule>
      <rule>Use `pathlib.Path` over `os.path` for all file path operations.</rule>
      <rule>Use type hints on all function signatures.</rule>
      <rule>Handle errors gracefully with specific exception types, not bare `except:`.</rule>
    </backend>

    <database>
      <rule>All SQL scripts, queries, and schema designs must prioritize Disaster Recovery (DR).</rule>
      <rule>Always include safe transaction handling (e.g., COMMIT/ROLLBACK logic).</rule>
      <rule>Structure migrations and schema updates to be non-destructive and easily reversible.</rule>
      <rule>Current state management uses JSON flat files. If migrating to a DB, follow DR principles from day one.</rule>
    </database>
  </domain_specific_rules>

  <security>
    <rule>Never commit API keys, passwords, or PII to version control.</rule>
    <rule>config.json is gitignored. config.example.json contains placeholder values.</rule>
    <rule>Rotate any key that has ever been committed to a public repo.</rule>
  </security>
</system_instructions>
