//! BM25 打分器：中文按「一元 + 二元」切词，英文按单词，无外部依赖。
//!
//! 词法与向量只是打分器不同——实验的因子是索引粒度，打分器是可替换的实例。

use std::collections::HashMap;

const K1: f64 = 1.2;
const B: f64 = 0.75;

/// 切词：ASCII 词小写化，CJK 连串出一元与二元。
pub fn tokenize(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut ascii = String::new();
    let mut cjk: Vec<char> = Vec::new();

    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() {
            flush_cjk(&mut cjk, &mut out);
            ascii.push(ch.to_ascii_lowercase());
        } else if is_cjk(ch) {
            flush_ascii(&mut ascii, &mut out);
            cjk.push(ch);
        } else {
            flush_ascii(&mut ascii, &mut out);
            flush_cjk(&mut cjk, &mut out);
        }
    }
    flush_ascii(&mut ascii, &mut out);
    flush_cjk(&mut cjk, &mut out);
    out
}

fn flush_ascii(buf: &mut String, out: &mut Vec<String>) {
    if !buf.is_empty() {
        out.push(std::mem::take(buf));
    }
}

fn flush_cjk(buf: &mut Vec<char>, out: &mut Vec<String>) {
    for (i, ch) in buf.iter().enumerate() {
        out.push(ch.to_string());
        if i + 1 < buf.len() {
            let mut bi = String::with_capacity(2);
            bi.push(*ch);
            bi.push(buf[i + 1]);
            out.push(bi);
        }
    }
    buf.clear();
}

fn is_cjk(ch: char) -> bool {
    matches!(ch,
        '\u{4E00}'..='\u{9FFF}' | '\u{3400}'..='\u{4DBF}' | '\u{F900}'..='\u{FAFF}')
}

/// BM25 索引（构建后只读，检索结果按（分数降序, 序号升序）排序——同输入必同输出）。
pub struct Bm25 {
    doc_len: Vec<usize>,
    avg_dl: f64,
    df: HashMap<String, usize>,
    tfs: Vec<HashMap<String, usize>>,
    n: f64,
}

impl Bm25 {
    pub fn new(docs: &[String]) -> Self {
        let mut doc_len = Vec::with_capacity(docs.len());
        let mut df: HashMap<String, usize> = HashMap::new();
        let mut tfs = Vec::with_capacity(docs.len());

        for text in docs {
            let tokens = tokenize(text);
            doc_len.push(tokens.len());
            let mut tf: HashMap<String, usize> = HashMap::new();
            for t in tokens {
                *tf.entry(t).or_insert(0) += 1;
            }
            for key in tf.keys() {
                *df.entry(key.clone()).or_insert(0) += 1;
            }
            tfs.push(tf);
        }

        let total: usize = doc_len.iter().sum();
        let avg_dl = if doc_len.is_empty() {
            1.0
        } else {
            total as f64 / doc_len.len() as f64
        };
        let n = doc_len.len() as f64;
        Self {
            doc_len,
            avg_dl,
            df,
            tfs,
            n,
        }
    }

    pub fn search(&self, query: &str, top_k: usize) -> Vec<(usize, f64)> {
        let mut qtf: HashMap<String, usize> = HashMap::new();
        for t in tokenize(query) {
            *qtf.entry(t).or_insert(0) += 1;
        }
        // 查询词排序后遍历：浮点求和顺序固定，同输入必同输出
        let mut terms: Vec<(&String, &usize)> = qtf.iter().collect();
        terms.sort_by(|a, b| a.0.cmp(b.0));

        let mut scored: Vec<(usize, f64)> = Vec::new();
        for (i, tf) in self.tfs.iter().enumerate() {
            let mut score = 0.0;
            for &(term, q_count) in &terms {
                let Some(&f) = tf.get(term) else { continue };
                let df = self.df.get(term).copied().unwrap_or(0) as f64;
                let idf = ((self.n - df + 0.5) / (df + 0.5) + 1.0).ln();
                let tf_v = f as f64;
                let denom = tf_v + K1 * (1.0 - B + B * self.doc_len[i] as f64 / self.avg_dl);
                score += idf * (tf_v * (K1 + 1.0) / denom) * (*q_count as f64);
            }
            if score > 0.0 {
                scored.push((i, score));
            }
        }
        scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap().then(a.0.cmp(&b.0)));
        scored.truncate(top_k);
        scored
    }
}
