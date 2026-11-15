export const routes=['实验档案','事件核对','实验裁决','发布记录','更正单'];export function page(){return Number(location.hash.slice(1))||0}
