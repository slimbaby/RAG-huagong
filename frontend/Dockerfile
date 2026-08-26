# ---- 依赖安装层（dev/build 共用，利用缓存）----
FROM node:20-alpine AS deps
WORKDIR /app
COPY package*.json ./
# 保留 package-lock.json 并优先 npm ci，保证依赖可复现
RUN npm ci || npm install

# ---- 开发目标：Vite dev server（docker-compose 使用此层，保留 HMR）----
FROM deps AS dev
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]

# ---- 构建目标：产出静态文件 ----
FROM deps AS build
COPY . .
RUN npm run build

# ---- 生产目标：nginx 静态服务 + API 反代 ----
FROM nginx:alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
