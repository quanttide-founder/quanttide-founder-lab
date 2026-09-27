//! 语料装载：语料边界由工具箱仓库模型决定，单元带模型坐标（类型、标题、行区间、分级）。
//!
//! 两臂共用同一文件集——公平性首先由「同一批文件」保证，差别只在索引粒度。

use std::collections::HashMap;
use std::error::Error;
use std::fs;
use std::path::Path;

use quanttide_founder::InsightGrade;
use quanttide_founder::fiction::repository::FictionRepository;
use quanttide_founder::memory::repository::MemoryRepository;

/// 语料文档类型（模型坐标之一）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Journal,
    Profile,
    Insight,
    Roadmap,
    Chapter,
    Emotion,
    NovelMeta,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Journal => "journal",
            Kind::Profile => "profile",
            Kind::Insight => "insight",
            Kind::Roadmap => "roadmap",
            Kind::Chapter => "chapter",
            Kind::Emotion => "emotion",
            Kind::NovelMeta => "novel_meta",
        }
    }

    /// fiction 侧类型——跨域联想的中性区（不享受路由亲和，也不被打压）。
    pub fn is_fiction(self) -> bool {
        matches!(self, Kind::Chapter | Kind::Emotion | Kind::NovelMeta)
    }
}

/// 证据分级：洞察文档「已确认 / 假说」节。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tier {
    Confirmed,
    Hypothesis,
}

/// 一个语料文件。
#[derive(Debug, Clone)]
pub struct Doc {
    /// 相对 `assets/` 的路径，`/` 分隔。
    pub path: String,
    pub kind: Kind,
    pub text: String,
}

/// 结构化检索单元：带完整模型坐标。
#[derive(Debug, Clone)]
pub struct Unit {
    pub path: String,
    pub kind: Kind,
    pub title: String,
    /// 1-based 起止行（含端）。
    pub line_start: usize,
    pub line_end: usize,
    pub text: String,
    pub tier: Option<Tier>,
}

impl Unit {
    /// 坐标串：`kind:line_start-line_end`，检索命中时随结果打印。
    pub fn coord(&self) -> String {
        format!(
            "{}:{}-{}",
            self.kind.as_str(),
            self.line_start,
            self.line_end
        )
    }
}

/// 路径 →（章节标题 → 分级）。
pub type TierMap = HashMap<String, HashMap<String, Tier>>;

/// 装载语料与洞察分级表。
///
/// 语料边界 = 工具箱模型覆盖的层：memory 的 journal / profile / insight / roadmap，
/// fiction 的章节、情绪日记与小说根部元文件。`intention`、`report` 等尚未建模的层
/// 不进语料（两臂一致）。
pub fn load_corpus(assets: &Path) -> Result<(Vec<Doc>, TierMap), Box<dyn Error>> {
    let mut docs: Vec<Doc> = Vec::new();
    let mut tier_map: TierMap = HashMap::new();
    let rel = |p: &Path| -> String {
        p.strip_prefix(assets)
            .unwrap_or(p)
            .to_string_lossy()
            .replace('\\', "/")
    };

    let mem = MemoryRepository::load(assets.join("memory"))?;
    for set in &mem.sets {
        for entry in &set.journals {
            docs.push(Doc {
                path: rel(Path::new(&entry.path)),
                kind: Kind::Journal,
                text: entry.content.clone(),
            });
        }
        for d in &set.profiles {
            docs.push(Doc {
                path: rel(Path::new(&d.path)),
                kind: Kind::Profile,
                text: fs::read_to_string(&d.path)?,
            });
        }
        for d in &set.insights {
            let path = rel(Path::new(&d.path));
            let text = fs::read_to_string(&d.path)?;
            let mut by_title = HashMap::new();
            for section in &d.sections {
                if let Some(grade) = section.grade {
                    by_title.insert(
                        section.title.clone(),
                        match grade {
                            InsightGrade::Confirmed => Tier::Confirmed,
                            InsightGrade::Hypothesis => Tier::Hypothesis,
                        },
                    );
                }
            }
            tier_map.insert(path.clone(), by_title);
            docs.push(Doc {
                path,
                kind: Kind::Insight,
                text,
            });
        }
        for d in &set.roadmaps {
            docs.push(Doc {
                path: rel(Path::new(&d.path)),
                kind: Kind::Roadmap,
                text: fs::read_to_string(&d.path)?,
            });
        }
    }

    let fic = FictionRepository::load(assets.join("fiction"))?;
    for novel in &fic.novels {
        for ch in novel.all_chapters() {
            docs.push(Doc {
                path: rel(&ch.file),
                kind: Kind::Chapter,
                text: ch.content.clone(),
            });
        }
        // 小说根部元文件（index/outline/worldview/decision）
        for entry in fs::read_dir(&novel.directory)? {
            let p = entry?.path();
            if !p.is_file() {
                continue;
            }
            let name = p
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            if !name.ends_with(".md") || name == "README.md" {
                continue;
            }
            docs.push(Doc {
                path: rel(&p),
                kind: Kind::NovelMeta,
                text: fs::read_to_string(&p)?,
            });
        }
    }
    for d in &fic.observation.emotional_diaries {
        docs.push(Doc {
            path: rel(&d.file),
            kind: Kind::Emotion,
            text: d.content.clone(),
        });
    }

    docs.sort_by(|a, b| a.path.cmp(&b.path));
    docs.dedup_by(|a, b| a.path == b.path);
    Ok((docs, tier_map))
}

/// 单元再切的字数上限（非空白字符数），超出按段落继续分。
const MAX_UNIT_CHARS: usize = 900;

/// 把语料切成单元：标题行与 `---` 分隔线是边界，超长单元按段落续分。
pub fn build_units(docs: &[Doc], tier_map: &HashMap<String, HashMap<String, Tier>>) -> Vec<Unit> {
    let mut units = Vec::new();
    for doc in docs {
        let stem = doc
            .path
            .rsplit('/')
            .next()
            .unwrap_or(&doc.path)
            .trim_end_matches(".md")
            .to_string();
        let lines: Vec<&str> = doc.text.split('\n').collect();
        let mut heading: Option<String> = None;
        let mut h2: Option<String> = None;
        let mut start = 1usize;
        let mut buf: Vec<&str> = Vec::new();

        for (i, line) in lines.iter().enumerate() {
            let ln = i + 1;
            let heading_at = heading_level(line);
            let is_sep = is_separator(line);

            if (heading_at.is_some() || is_sep) && !buf.is_empty() {
                push_unit(
                    &mut units,
                    doc,
                    &stem,
                    heading.as_deref(),
                    h2.as_deref(),
                    start,
                    ln - 1,
                    &buf,
                    tier_map,
                );
                buf.clear();
            }

            if let Some((level, text)) = heading_at {
                heading = Some(text);
                if level == 2 {
                    h2 = Some(heading.clone().unwrap_or_default());
                }
                start = ln;
                buf.push(line);
                continue;
            }
            if is_sep {
                // 分隔线之后是新的会话段，标题回退到文件名
                heading = None;
                h2 = None;
                start = ln + 1;
                continue;
            }
            if buf.is_empty() {
                start = ln;
            }
            buf.push(line);
        }
        if !buf.is_empty() {
            push_unit(
                &mut units,
                doc,
                &stem,
                heading.as_deref(),
                h2.as_deref(),
                start,
                lines.len(),
                &buf,
                tier_map,
            );
        }
    }
    units
}

#[allow(clippy::too_many_arguments)]
fn push_unit(
    out: &mut Vec<Unit>,
    doc: &Doc,
    stem: &str,
    heading: Option<&str>,
    h2: Option<&str>,
    line_start: usize,
    line_end: usize,
    lines: &[&str],
    tier_map: &HashMap<String, HashMap<String, Tier>>,
) {
    let text = lines.join("\n").trim().to_string();
    if text.is_empty() {
        return;
    }
    let title = heading.unwrap_or(stem).to_string();
    let tier = h2
        .and_then(|t| tier_map.get(&doc.path).and_then(|m| m.get(t)))
        .copied();

    if text.chars().filter(|c| !c.is_whitespace()).count() <= MAX_UNIT_CHARS {
        out.push(Unit {
            path: doc.path.clone(),
            kind: doc.kind,
            title,
            line_start,
            line_end,
            text,
            tier,
        });
        return;
    }

    // 超长单元按段落打包，保持行号
    let mut parts: Vec<(usize, usize, Vec<&str>)> = Vec::new();
    let mut cur_lines: Vec<&str> = Vec::new();
    let mut cur_chars = 0usize;
    let mut cur_start = line_start;
    for (j, line) in lines.iter().enumerate() {
        let wide = line.chars().filter(|c| !c.is_whitespace()).count();
        if cur_chars + wide > MAX_UNIT_CHARS && !cur_lines.is_empty() {
            parts.push((
                cur_start,
                line_start + j - 1,
                std::mem::take(&mut cur_lines),
            ));
            cur_chars = 0;
            cur_start = line_start + j;
        }
        cur_lines.push(line);
        cur_chars += wide;
    }
    if !cur_lines.is_empty() {
        parts.push((cur_start, line_end, cur_lines));
    }

    let multi = parts.len() > 1;
    for (idx, (s, e, ls)) in parts.into_iter().enumerate() {
        let t = ls.join("\n").trim().to_string();
        if t.is_empty() {
            continue;
        }
        let title = if multi {
            format!("{title}（段{}）", idx + 1)
        } else {
            title.clone()
        };
        out.push(Unit {
            path: doc.path.clone(),
            kind: doc.kind,
            title,
            line_start: s,
            line_end: e,
            text: t,
            tier,
        });
    }
}

/// 标题行 →（层级, 文本）。
pub fn heading_level(line: &str) -> Option<(usize, String)> {
    let hashes = line.chars().take_while(|c| *c == '#').count();
    if hashes == 0 || hashes > 6 {
        return None;
    }
    let rest = line.get(hashes..)?;
    let text = rest.strip_prefix(' ')?;
    Some((hashes, text.trim().to_string()))
}

/// `---` / `--` 类会话分隔线（与工具箱 `JournalEntry::segments` 同语义）。
fn is_separator(line: &str) -> bool {
    let t = line.trim();
    t.len() >= 2 && t.chars().all(|c| c == '-')
}
