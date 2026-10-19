/**
 * Content collections. Articles are YAML files in src/content/articles/<year>/<slug>.yaml.
 *
 * An article's notes cite the data by id: a policy record (with the provision whose quote it
 * uses), or a research study and one of its key findings. Everything shown for a note is read
 * from the data at build time, never copied by hand, and the build fails if an id or index
 * does not exist or if the text's [n] markers and the notes disagree.
 */
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';
import { records, research } from './lib/data';

export const ARTICLE_TYPES = ['Explainer', 'Analysis', 'Roundup', 'State brief', 'Research digest', 'Opinion'] as const;

const recordNote = z.object({
  record: z.string(),
  /** Which of the record's provisions supplies the locator, quote and link (from 0). */
  provision: z.number().int().min(0).default(0),
});
const studyNote = z.object({
  study: z.string(),
  /** Which of the study's key findings is cited (from 0). */
  finding: z.number().int().min(0),
});
const cell = z.union([z.string(), z.object({ record: z.string(), text: z.string().optional() })]);
const block = z.union([
  z.object({ p: z.string() }),
  z.object({ h: z.string() }),
  z.object({
    table: z.object({
      title: z.string(),
      source: z.string(),
      columns: z.array(z.string()).min(1),
      rows: z.array(z.array(cell)).min(1),
    }),
  }),
]);

const articles = defineCollection({
  loader: glob({ pattern: '**/*.{yaml,yml}', base: './src/content/articles' }),
  schema: z
    .object({
      title: z.string().min(1),
      dek: z.string().min(1),
      type: z.enum(ARTICLE_TYPES),
      published: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, 'published must be YYYY-MM-DD'),
      /** The data release the article was written from, e.g. "October 2026". */
      data_release: z.string().min(1),
      brief: z.array(z.string()).min(1),
      caption: z.string().optional(),
      /** A few handwritten words on the index drawing, e.g. "must adopt a policy". */
      sketch_note: z.string().optional(),
      /** The index drawing; left out, it follows from the type and what the article cites. */
      drawing: z.enum(['map', 'desk', 'capitol', 'gavel', 'classroom', 'phone', 'calendar', 'lock']).optional(),
      body: z.array(block).min(1),
      notes: z.array(z.union([recordNote, studyNote])),
      /** Opinion pieces only. */
      author: z.object({ name: z.string().min(1), bio: z.string().min(1) }).optional(),
    })
    .superRefine((a, ctx) => {
      const issue = (message: string, path: (string | number)[] = []) => ctx.addIssue({ code: 'custom', message, path });
      const recs = new Map(records().map((r) => [r.id, r]));
      const studies = new Map(research().map((s) => [s.id, s]));
      a.notes.forEach((n, i) => {
        if ('record' in n) {
          const r = recs.get(n.record);
          if (!r) issue(`Note ${i + 1} cites record "${n.record}", which is not in the data.`, ['notes', i, 'record']);
          else if (!r.provisions[n.provision]) issue(`Note ${i + 1}: record "${n.record}" has no provision ${n.provision}.`, ['notes', i, 'provision']);
        } else {
          const s = studies.get(n.study);
          if (!s) issue(`Note ${i + 1} cites study "${n.study}", which is not in the research library.`, ['notes', i, 'study']);
          else if (!s.findings[n.finding]) issue(`Note ${i + 1}: study "${n.study}" has no key finding ${n.finding}.`, ['notes', i, 'finding']);
        }
      });
      a.body.forEach((b, i) => {
        if (!('table' in b)) return;
        b.table.rows.forEach((row, ri) => {
          if (row.length !== b.table.columns.length) issue(`Table row ${ri + 1} has ${row.length} cells for ${b.table.columns.length} columns.`, ['body', i, 'table', 'rows', ri]);
          row.forEach((c, ci) => {
            if (typeof c === 'string') return;
            const r = recs.get(c.record);
            if (!r) issue(`Table cell cites record "${c.record}", which is not in the data.`, ['body', i, 'table', 'rows', ri, ci]);
            else if (!r.identifier && !c.text) issue(`Record "${c.record}" has no identifier; give the cell a text.`, ['body', i, 'table', 'rows', ri, ci]);
          });
        });
      });
      // Every [n] marker must point at a note, and every note must be cited in the text.
      const cited = new Set<number>();
      for (const b of a.body) if ('p' in b) for (const m of b.p.matchAll(/\[(\d+)\]/g)) cited.add(Number(m[1]));
      for (const n of cited) if (n < 1 || n > a.notes.length) issue(`The text cites [${n}] but there are ${a.notes.length} notes.`, ['body']);
      a.notes.forEach((_, i) => { if (!cited.has(i + 1)) issue(`Note ${i + 1} is never cited in the text.`, ['notes', i]); });
      if (a.type === 'Opinion' && !a.author) issue('An Opinion piece needs an author (name and bio).', ['author']);
      if (a.type !== 'Opinion' && a.author) issue('Only Opinion pieces carry an outside author.', ['author']);
    }),
});

export const collections = { articles };
