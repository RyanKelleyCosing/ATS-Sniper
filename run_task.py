from generate_tailored_resume import generate_resume_outputs
from utils.application_packages import write_cover_letter_docx
import os
from pathlib import Path

resume_md = r'outputs\2026-04-23_Systems_Administrator\resume_Systems_Administrator.md'
output_dir = r'outputs\2026-04-23_Systems_Administrator'
cl_txt = r'outputs\2026-04-23_Systems_Administrator\cover_letter_Systems_Administrator.txt'
cl_docx = Path(r'outputs\2026-04-23_Systems_Administrator\cover_letter_Systems_Administrator.docx')

print('Generating resume outputs...')
try:
    paths = generate_resume_outputs(resume_md, output_dir)
    print(f'Resume paths: {paths}')
except Exception as e:
    print(f'Error generating resume: {e}')

if os.path.exists(cl_txt):
    print('Writing cover letter docx...')
    with open(cl_txt, 'r', encoding='utf-8') as f:
        text = f.read()
    try:
        write_cover_letter_docx(text, cl_docx)
        print(f'Cover letter written to: {cl_docx}')
    except Exception as e:
        print(f'Error writing cover letter: {e}')
else:
    print(f'Warning: {cl_txt} not found.')
