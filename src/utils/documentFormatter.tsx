import React from 'react';

/**
 * Normalizes and cleans document titles to be human-readable and legal-grade.
 * 
 * Precedence & rules:
 * - Decodes percent-encoding (e.g. %20 -> space, %26 -> &, %28 -> (, %29 -> ))
 * - Strips document file extensions (.pdf, .txt, .md, .html, .htm, .doc, .docx)
 * - Replaces underscores with spaces when used as word separators
 * - Strips technical numeric / CMS / hash prefixes (e.g. '17497725 ' or hex IDs)
 * - Retains legitimate years (e.g. '2024') and court case citations
 * - Normalizes redundant whitespace
 */
export function formatDocumentTitle(title?: string | null): string {
  if (!title || !title.trim()) {
    return 'Expediente documental';
  }

  let clean = title.trim();

  // If path or URL, take last component
  if (clean.includes('/') || clean.includes('\\')) {
    const parts = clean.split(/[/\\]/);
    clean = parts[parts.length - 1] || clean;
  }

  // URL decode if percent-encoded
  try {
    clean = decodeURIComponent(clean.replace(/\+/g, ' '));
  } catch {
    // If malformed URI sequence, keep as is
  }

  // Strip common file extensions (case-insensitive)
  clean = clean.replace(/\.(pdf|txt|md|html?|docx?)$/i, '').trim();

  // Replace underscores with spaces if predominantly underscores
  if (clean.includes('_') && (clean.split('_').length - 1 >= clean.split(' ').length - 1 || !clean.includes(' '))) {
    clean = clean.replace(/_/g, ' ');
  }

  // Strip technical numeric (5+ digits) or hexadecimal hash prefixes (16+ chars)
  clean = clean.replace(/^(?:[a-f0-9]{16,}|\d{5,})[\s_\-]+/i, '').trim();

  // Collapse multiple whitespaces
  clean = clean.replace(/\s+/g, ' ').trim();

  return clean || 'Expediente documental';
}

/**
 * Regex identifying legal structural markers:
 * - Numbered paragraphs: '1. ', '12) '
 * - Sub-clauses: '(a) ', '(i) ', '(iv) '
 * - Bullet points: '• ', '- ', '* '
 * - Uppercase legal headings: 'BETWEEN:', 'ORDER:', 'HEADING TITLE'
 */
const NUMBERED_CLAUSE_REGEX = /^(?:\d+[\.\)]|\([a-z\divxlcdm]+\))\s+/i;
const BULLET_REGEX = /^[\u2022\-\*]\s+/;
const ALL_CAPS_HEADING_REGEX = /^[A-Z0-9\s,\.\-—:]{4,}:?$/;

interface ParsedItem {
  text: string;
  type: 'heading' | 'numbered' | 'bullet' | 'paragraph';
}

/**
 * Parses lines within a block, detecting inline headings, numbered clauses,
 * bullet items, and unwrapping soft linebreaks for regular sentences.
 */
function parseBlockItems(rawBlock: string): ParsedItem[] {
  const lines = rawBlock.split('\n').map((l) => l.trim()).filter(Boolean);
  if (lines.length === 0) return [];

  const result: ParsedItem[] = [];
  let currentParagraph: string[] = [];

  const flushParagraph = () => {
    if (currentParagraph.length > 0) {
      const fullText = currentParagraph.join(' ');
      const isNumbered = NUMBERED_CLAUSE_REGEX.test(fullText);
      const isBullet = BULLET_REGEX.test(fullText);
      result.push({
        text: fullText,
        type: isNumbered ? 'numbered' : isBullet ? 'bullet' : 'paragraph',
      });
      currentParagraph = [];
    }
  };

  for (const line of lines) {
    const isHeading =
      (ALL_CAPS_HEADING_REGEX.test(line) && line.length < 80 && !NUMBERED_CLAUSE_REGEX.test(line)) ||
      (line.endsWith(':') && line.length < 60);

    const isNumbered = NUMBERED_CLAUSE_REGEX.test(line);
    const isBullet = BULLET_REGEX.test(line);

    if (isHeading) {
      flushParagraph();
      result.push({ text: line, type: 'heading' });
    } else if (isNumbered || isBullet) {
      flushParagraph();
      currentParagraph.push(line);
    } else {
      currentParagraph.push(line);
    }
  }

  flushParagraph();
  return result;
}

/**
 * Renders document body content with comfortable typography, paragraph reflowing,
 * and visual identification of legal structural elements.
 */
export function renderDocumentContent(content?: string | null): React.ReactNode {
  if (!content || !content.trim()) {
    return null;
  }

  // Split into paragraph blocks delimited by double (or more) newlines
  const rawBlocks = content.trim().split(/\n\s*\n+/);

  return (
    <div className="document-body space-y-4 font-serif text-slate-800 text-[15px] leading-relaxed select-text">
      {rawBlocks.map((rawBlock, blockIdx) => {
        const trimmedBlock = rawBlock.trim();
        if (!trimmedBlock) return null;

        // Standalone page number marker (e.g. '1', '24')
        if (/^\d{1,4}$/.test(trimmedBlock)) {
          return (
            <div
              key={`page-${blockIdx}`}
              className="text-center text-xs font-sans text-slate-400 py-3 border-b border-slate-100 my-4 select-none tracking-wider uppercase"
            >
              — Página {trimmedBlock} —
            </div>
          );
        }

        const items = parseBlockItems(trimmedBlock);

        return (
          <div key={`block-${blockIdx}`} className="space-y-2">
            {items.map((item, itemIdx) => {
              if (item.type === 'heading') {
                return (
                  <h3
                    key={`item-${itemIdx}`}
                    className="font-serif font-bold text-slate-900 mt-5 mb-2 text-base sm:text-lg tracking-wide border-b border-slate-100 pb-1"
                  >
                    {item.text}
                  </h3>
                );
              }

              if (item.type === 'numbered') {
                return (
                  <p
                    key={`item-${itemIdx}`}
                    className="font-serif text-slate-800 pl-3 border-l-2 border-slate-200 hover:border-navy-900/40 transition-colors py-0.5"
                  >
                    {item.text}
                  </p>
                );
              }

              if (item.type === 'bullet') {
                return (
                  <p key={`item-${itemIdx}`} className="font-serif text-slate-800 pl-4 py-0.5">
                    {item.text}
                  </p>
                );
              }

              return (
                <p key={`item-${itemIdx}`} className="font-serif text-slate-800 leading-relaxed">
                  {item.text}
                </p>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
