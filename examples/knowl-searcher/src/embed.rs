//! 向量打分器：OpenAI 兼容的 embeddings 端点，HTTP 复用 quanttide-agent 的 `UreqClient`。
//!
//! 环境变量：`KNOWL_EMBED_BASE_URL` / `KNOWL_EMBED_API_KEY` / `KNOWL_EMBED_MODEL`，
//! 缺 key 时回退 `GLM_API_KEY` + 智谱 embedding-3。

use quanttide_agent::HttpClient;
use quanttide_agent::llm::UreqClient;
use serde_json::json;

/// 嵌入文本的字符截断（保守低于常见 512 token 上限）。
pub const EMBED_CHARS: usize = 480;

pub struct Embedder {
    base: String,
    key: String,
    model: String,
    client: UreqClient,
}

impl Embedder {
    pub fn from_env() -> Result<Self, String> {
        let base = std::env::var("KNOWL_EMBED_BASE_URL")
            .unwrap_or_else(|_| "https://open.bigmodel.cn/api/paas/v4".to_string());
        let key = std::env::var("KNOWL_EMBED_API_KEY")
            .or_else(|_| std::env::var("GLM_API_KEY"))
            .map_err(|_| {
                "缺少 embedding key：设置 KNOWL_EMBED_API_KEY 或 GLM_API_KEY".to_string()
            })?;
        let model =
            std::env::var("KNOWL_EMBED_MODEL").unwrap_or_else(|_| "embedding-3".to_string());
        Ok(Self {
            base: base.trim_end_matches('/').to_string(),
            key,
            model,
            client: UreqClient,
        })
    }

    pub fn model(&self) -> &str {
        &self.model
    }

    /// 批量嵌入（每批 16 条），进度打到 stderr。
    pub fn embed_all(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, String> {
        let mut out = Vec::with_capacity(texts.len());
        for batch in texts.chunks(16) {
            let body = json!({ "model": self.model, "input": batch });
            let resp = self
                .client
                .post_json(
                    &format!("{}/embeddings", self.base),
                    &format!("Bearer {}", self.key),
                    &body,
                )
                .map_err(|e| e.to_string())?;
            let mut data = resp
                .get("data")
                .and_then(|d| d.as_array())
                .ok_or("embedding 响应缺少 data")?
                .clone();
            data.sort_by_key(|v| v.get("index").and_then(|x| x.as_u64()).unwrap_or(0));
            for item in data {
                let vec = item
                    .get("embedding")
                    .and_then(|e| e.as_array())
                    .ok_or("embedding 响应缺少 embedding")?
                    .iter()
                    .filter_map(|x| x.as_f64())
                    .map(|x| x as f32)
                    .collect::<Vec<_>>();
                out.push(vec);
            }
            eprint!("\r  已嵌入 {}/{} 条", out.len(), texts.len());
        }
        eprintln!();
        if out.len() != texts.len() {
            return Err(format!("嵌入数量不符：{} != {}", out.len(), texts.len()));
        }
        Ok(out)
    }
}

/// 截断到嵌入上限。
pub fn embed_text(text: &str) -> String {
    text.chars().take(EMBED_CHARS).collect()
}

/// 余弦相似度。
pub fn cosine(a: &[f32], b: &[f32]) -> f64 {
    let dot: f64 = a
        .iter()
        .zip(b)
        .map(|(x, y)| (*x as f64) * (*y as f64))
        .sum();
    let na: f64 = a
        .iter()
        .map(|x| (*x as f64) * (*x as f64))
        .sum::<f64>()
        .sqrt();
    let nb: f64 = b
        .iter()
        .map(|y| (*y as f64) * (*y as f64))
        .sum::<f64>()
        .sqrt();
    if na == 0.0 || nb == 0.0 {
        0.0
    } else {
        dot / (na * nb)
    }
}
