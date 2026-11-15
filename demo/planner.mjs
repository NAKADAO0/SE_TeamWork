export function decompose(spec) {
  if (!spec || !spec.product?.trim() || !Array.isArray(spec.requirements) || !spec.requirements.length)
    throw Error("请填写产品名称和需求列表");
  const ids = new Set();
  return spec.requirements.flatMap(({id, goal, acceptance}) => {
    if (!/^[A-Za-z0-9_-]+$/.test(id ?? "") || !goal?.trim() || !acceptance?.trim() || ids.has(id))
      throw Error("需求需要唯一编号、目标和验收条件");
    ids.add(id);
    return ["实现", "验证"].map((kind,i) => ({
      id: id + (i ? "-V" : "-I"), source:id, title:kind + goal, acceptance,
      role:i ? "qa" : "developer", depends_on:i ? [id+"-I"] : []
    }));
  });
}
