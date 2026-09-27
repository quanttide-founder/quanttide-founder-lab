//! 纯 RAG 臂的切块：字符滑窗，只有文件路径与块序号——没有类型、没有章节标题、没有分级。
//!
//! 这是对照的意义所在：两臂读同一批文件，差别只在「打不打结构」。

/// 一个检索块。
#[derive(Debug, Clone)]
pub struct Chunk {
    pub path: String,
    pub index: usize,
    /// 1-based 起始行（仅用于定位打印，不参与判定）。
    pub line_start: usize,
    pub text: String,
}

/// 滑窗切块：窗口 400 字符、步长 320（重叠 80），确定性。
pub fn chunk_docs(docs: &[crate::corpus::Doc], window: usize, step: usize) -> Vec<Chunk> {
    let mut chunks = Vec::new();
    for doc in docs {
        let chars: Vec<char> = doc.text.chars().collect();
        let mut start = 0usize;
        let mut index = 0usize;
        while start < chars.len() {
            let end = (start + window).min(chars.len());
            let text: String = chars[start..end].iter().collect();
            if !text.trim().is_empty() {
                let line_start = chars[..start].iter().filter(|c| **c == '\n').count() + 1;
                chunks.push(Chunk {
                    path: doc.path.clone(),
                    index,
                    line_start,
                    text,
                });
            }
            index += 1;
            if end == chars.len() {
                break;
            }
            start += step;
        }
    }
    chunks
}
