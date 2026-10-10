FROM node:22-alpine AS build
WORKDIR /app
COPY app/package.json ./
RUN npm install
COPY app .
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/start-nginx.sh /usr/local/bin/start-nginx
RUN chmod +x /usr/local/bin/start-nginx
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
CMD ["/usr/local/bin/start-nginx"]

